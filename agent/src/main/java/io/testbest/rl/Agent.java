package io.testbed.rl;

import org.deeplearning4j.nn.api.OptimizationAlgorithm;
import org.deeplearning4j.nn.conf.MultiLayerConfiguration;
import org.deeplearning4j.nn.conf.NeuralNetConfiguration;
import org.deeplearning4j.nn.conf.inputs.InputType;
import org.deeplearning4j.nn.conf.layers.ConvolutionLayer;
import org.deeplearning4j.nn.conf.layers.DenseLayer;
import org.deeplearning4j.nn.conf.layers.GlobalPoolingLayer;
import org.deeplearning4j.nn.conf.layers.OutputLayer;
import org.deeplearning4j.nn.conf.layers.PoolingType;
import org.deeplearning4j.nn.multilayer.MultiLayerNetwork;
import org.nd4j.linalg.activations.Activation;
import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.factory.Nd4j;
import org.nd4j.linalg.learning.config.Adam;
import org.nd4j.linalg.lossfunctions.LossFunctions;

import java.io.Serializable;
import java.util.*;

import java.io.FileWriter;
import java.io.IOException;
import java.io.PrintWriter;

/**
 * Double-Dueling DQN Agent (CTDE-style) with PER, conv front-end, and annealing.
 * - Children call decide(); sink calls endPhase() to close episodes & train.
 * - Trains EVERY phase: exactly one mini-batch (size=32).
 * - Target network update every 5 steps. Replay capacity=1000.
 * - Dueling head: Q = V + (A - mean(A))
 * - Annealing: epsilon (exploration) and PER beta (IS weights) over phases.
 */
public class Agent implements Serializable {

    private static final String LOG_PATH = "/workspace/testbed/logs/agent.log";
    private static PrintWriter logger;
    static {
        try {
            logger = new PrintWriter(new FileWriter(LOG_PATH, true), true);
            logger.println("=== Agent started ===");
        } catch (IOException e) {
            e.printStackTrace();
            logger = new PrintWriter(System.err, true);
        }
    }
    private static void log(String msg) {
        if (logger != null) { logger.println(System.currentTimeMillis() + " " + msg); logger.flush(); }
    }

    // -------- Controller inputs --------
    public static class Counters {
        public int generated, delivered, dropped;
        public double residualEnergy;
        public int etx, hopCount, rankViolations;
    }

    // -------- Episode & Replay --------
    private static class Episode {
        final double[] sFlat; final int a; final int parentId;
        final double hcSnap, reSnap, qlrSnap;
        Episode(double[] sFlat, int a, int parentId, double hcSnap, double reSnap, double qlrSnap) {
            this.sFlat = sFlat; this.a = a; this.parentId = parentId;
            this.hcSnap = hcSnap; this.reSnap = reSnap; this.qlrSnap = qlrSnap;
        }
    }

    private static class Transition {
        final double[] s; final int a; final double r;
        final double[] s2; final boolean done; final boolean[] valid2;
        Transition(double[] s, int a, double r, double[] s2, boolean done, boolean[] valid2) {
            this.s = s; this.a = a; this.r = r; this.s2 = s2; this.done = done; this.valid2 = valid2;
        }
    }

    // -------- Feature indices (unchanged) --------
    private static final int IDX_ETX=0, IDX_HC=1, IDX_RE=2, IDX_QLR=3, IDX_BDI=4, IDX_WR=5,
                             IDX_CC=6, IDX_PC=7, IDX_SI=8, IDX_GEN=9, IDX_FWD=10, IDX_QLOSS=11;

    // -------- Hyperparams (paper-aligned) --------
    private final int k;
    private final int Ftotal;
    private final boolean[] featureMask;
    private final int Factive;

    // Epsilon & PER-beta annealing (linear over phases)
    private int phaseCount = 0;
    private double epsilonStart = 0.30, epsilonEnd = 0.01;    // exploration
    private int epsilonAnnealPhases = 500;                    // phases to reach epsilonEnd
    private double betaStart = 0.40, betaEnd = 1.00;          // PER IS exponent
    private int betaAnnealPhases = 500;                       // phases to reach betaEnd

    private double epsilon = epsilonStart; // current eps
    private double perBeta  = betaStart;   // current beta

    private double gamma   = 0.90;
    private int batchSize  = 32;
    private int targetUpdateEvery = 5;

    private double w1_QU = 1.0;
    private double w2_ECR = 1.0;

    private final double initialEnergy;

    // PER
    private static final int REPLAY_CAPACITY = 1_000;
    private final Deque<Transition> replay = new ArrayDeque<>(REPLAY_CAPACITY);
    private final Map<Transition, Double> pri = new IdentityHashMap<>();
    private double perAlpha = 0.6;   // priority exponent

    // -------- State --------
    private final Map<Integer, Episode> open = new HashMap<>();
    private final Random rng = new Random(1234);

    // Dueling networks: shared-style conv front, separate heads
    private final MultiLayerNetwork onlineAdv;   // outputs k advantages
    private final MultiLayerNetwork onlineVal;   // outputs 1 value
    private final MultiLayerNetwork targetAdv;
    private final MultiLayerNetwork targetVal;

    private long trainSteps = 0;
    private final Map<Integer, Integer> activePos = new HashMap<>();

    // -------- Construction --------
    public Agent(int k, boolean[] mask, double initialEnergyJ) {
        if (k <= 0) throw new IllegalArgumentException("k must be > 0");
        if (mask == null || mask.length == 0) throw new IllegalArgumentException("mask must be non-empty");
        this.k = k;
        this.Ftotal = mask.length;
        this.featureMask = Arrays.copyOf(mask, mask.length);
        this.initialEnergy = initialEnergyJ > 0 ? initialEnergyJ : 1.0;

        int cnt = 0;
        for (int j=0; j<mask.length; j++) if (mask[j]) { activePos.put(j, cnt); cnt++; }
        if (cnt == 0) throw new IllegalArgumentException("featureMask has no active features");
        this.Factive = cnt;

        Nd4j.getRandom().setSeed(123);

        this.onlineAdv = buildConvNet(k);
        this.onlineVal = buildConvNet(1);
        this.onlineAdv.init();
        this.onlineVal.init();

        this.targetAdv = buildConvNet(k);
        this.targetVal = buildConvNet(1);
        this.targetAdv.init();
        this.targetVal.init();

        hardSyncTarget();
    }

    private MultiLayerNetwork buildConvNet(int outDim) {
        // Input: [mb, 1, k, Factive]
        MultiLayerConfiguration conf = new NeuralNetConfiguration.Builder()
                .seed(123)
                .optimizationAlgo(OptimizationAlgorithm.STOCHASTIC_GRADIENT_DESCENT)
                .updater(new Adam(1e-3))
                .weightInit(org.deeplearning4j.nn.weights.WeightInit.XAVIER)
                .list()
                .layer(new ConvolutionLayer.Builder(3,3).stride(1,1).nIn(1).nOut(16).activation(Activation.RELU).build())
                .layer(new ConvolutionLayer.Builder(3,1).stride(1,1).nOut(32).activation(Activation.RELU).build())
                .layer(new GlobalPoolingLayer.Builder(PoolingType.AVG).build())
                .layer(new DenseLayer.Builder().nOut(128).activation(Activation.RELU).build())
                .layer(new OutputLayer.Builder(LossFunctions.LossFunction.MSE).activation(Activation.IDENTITY).nOut(outDim).build())
                .setInputType(InputType.convolutional(k, Factive, 1))
                .build();
        return new MultiLayerNetwork(conf);
    }

    // -------- Decide --------
    public synchronized int decide(int moteId,
                                   double[][] S,
                                   boolean[] valid,
                                   Counters countersNow,
                                   int[] candIds,
                                   double[] hcArr,
                                   double[] reArr,
                                   double[] qlrArr) {

        double[] flat = flattenState(S);

        // Close previous episode -> transition
        Episode prev = open.remove(moteId);
        if (prev != null) {
            double rPrev = computeRewardForPrevious(prev, candIds, hcArr, reArr, qlrArr);
            addReplay(new Transition(prev.sFlat, prev.a, rPrev, flat, false, copyOrAllValid(valid)));
        }

        // ε-greedy over dueling Q
        double[] q = qValues(onlineAdv, onlineVal, flat);
        int greedy = argmaxMasked(q, valid);
        if (greedy < 0) greedy = 0;
        int a = (rng.nextDouble() < epsilon) ? randomValid(valid, k) : greedy;

        // Snapshot chosen parent's metrics
        int chosenParentId = 0; double hcSnap=0, reSnap=0, qlSnap=0;
        if (a >= 0 && candIds != null && a < candIds.length) {
            chosenParentId = candIds[a];
            hcSnap = valOrZero(hcArr, a);
            reSnap = valOrZero(reArr, a);
            qlSnap = valOrZero(qlrArr, a);
        }

        open.put(moteId, new Episode(flat, a, chosenParentId, hcSnap, reSnap, qlSnap));
        return a;
    }

    // -------- End Phase (train every phase: one mini-batch) --------
    public synchronized void endPhase() {
        // Close open episodes
        for (Map.Entry<Integer, Episode> e : open.entrySet()) {
            Episode ep = e.getValue();
            double r = rewardFromRank(ep.hcSnap, ep.reSnap, ep.qlrSnap);
            addReplay(new Transition(ep.sFlat, ep.a, r, ep.sFlat, true, null));
        }
        open.clear();

        // Train exactly ONE mini-batch per phase
        if (!replay.isEmpty()) {
            trainOneBatchPER();
        }

        // Phase-based annealing of epsilon and PER beta
        phaseCount++;
        double epsFrac  = Math.min(1.0, phaseCount / (double)Math.max(1, epsilonAnnealPhases));
        epsilon = epsilonStart + (epsilonEnd - epsilonStart) * epsFrac;
        double betaFrac = Math.min(1.0, phaseCount / (double)Math.max(1, betaAnnealPhases));
        perBeta = betaStart + (betaEnd - betaStart) * betaFrac;
    }

    // -------- Training (PER + Double-Dueling targets) --------
    private void trainOneBatchPER() {
        int D = replay.size();
        int bs = Math.min(batchSize, D);

        // Snapshot pool & priorities
        Transition[] pool = replay.toArray(new Transition[0]);
        double[] pArr = new double[D];
        double sumP = 0.0;
        for (int i=0;i<D;i++) {
            double p = pri.getOrDefault(pool[i], 1.0);
            p = Math.pow(Math.max(p, 1e-12), perAlpha);
            pArr[i] = p; sumP += p;
        }
        // Prefix sums
        double[] pref = new double[D];
        double c=0; for (int i=0;i<D;i++){ c+=pArr[i]; pref[i]=c; }
        final double total = c;

        // Stratified sampling
        int[] idxs = new int[bs];
        for (int i=0;i<bs;i++) {
            double u = ((i + rng.nextDouble())/bs) * total;
            int lo=0, hi=D-1;
            while (lo < hi) {
                int mid = (lo+hi)>>>1;
                if (pref[mid] >= u) hi = mid; else lo = mid+1;
            }
            idxs[i] = lo;
        }

        // Importance-sampling weights
        double[] is = new double[bs];
        double maxW = 1e-12;
        for (int i=0;i<bs;i++) {
            double Pi = pArr[idxs[i]] / total;
            double wi = Math.pow(D * Pi, -perBeta);
            is[i] = wi; if (wi > maxW) maxW = wi;
        }
        for (int i=0;i<bs;i++) is[i] /= maxW; // normalize to <=1

        // Build tensors for both heads
        INDArray Xadv = Nd4j.create(bs, 1, k, Factive);
        INDArray Xval = Nd4j.create(bs, 1, k, Factive);
        INDArray Yadv = Nd4j.create(bs, k);
        INDArray Yval = Nd4j.create(bs, 1);
        INDArray Madv = Nd4j.zeros(bs, k); // label mask (per-output weights)
        INDArray Mval = Nd4j.zeros(bs, 1);

        double[] newPriorities = new double[bs];
        Transition[] batch = new Transition[bs];

        for (int i=0;i<bs;i++) {
            Transition t = pool[idxs[i]];
            batch[i] = t;

            putConvFromFlat(Xadv, i, t.s);
            putConvFromFlat(Xval, i, t.s);

            // Current dueling outputs
            double[] A_curr = advOutput(onlineAdv, t.s);
            double V_curr   = valOutput(onlineVal, t.s);
            double meanA = mean(A_curr);
            double[] Q_curr = new double[k];
            for (int a=0;a<k;a++) Q_curr[a] = V_curr + (A_curr[a] - meanA);

            // Double DQN target with dueling combine
            double targetQ;
            if (t.done) {
                targetQ = t.r;
            } else {
                int aStar = argmaxMasked(qValues(onlineAdv, onlineVal, t.s2), t.valid2);
                if (aStar < 0) aStar = 0;
                double[] A_tgt = advOutput(targetAdv, t.s2);
                double V_tgt   = valOutput(targetVal, t.s2);
                double meanAt  = mean(A_tgt);
                double Qnext = V_tgt + (A_tgt[aStar] - meanAt);
                targetQ = t.r + gamma * Qnext;
            }

            // TD error for chosen action
            int aIdx = clamp(t.a, 0, k-1);
            double td = targetQ - Q_curr[aIdx];
            newPriorities[i] = Math.abs(td) + 1e-3;

            // Labels: Advantage (only chosen a supervised)
            double[] A_lbl = Arrays.copyOf(A_curr, k);
            A_lbl[aIdx] = targetQ - V_curr + meanA;
            Yadv.getRow(i).assign(Nd4j.createFromArray(A_lbl));
            Madv.putScalar(i, aIdx, is[i]);

            // Labels: Value
            double V_lbl = targetQ - (A_curr[aIdx] - meanA);
            Yval.putScalar(i, 0, V_lbl);
            Mval.putScalar(i, 0, is[i]);
        }

        // Fit both heads with label masks (weighted losses)
        onlineAdv.fit(Xadv, Yadv, null, Madv);
        onlineVal.fit(Xval, Yval, null, Mval);

        trainSteps++;
        if (trainSteps % Math.max(1, targetUpdateEvery) == 0) hardSyncTarget();

        // Update priorities
        for (int i=0;i<bs;i++) pri.put(batch[i], newPriorities[i]);
    }

    // -------- Q helpers (dueling combine) --------
    private double[] qValues(MultiLayerNetwork adv, MultiLayerNetwork val, double[] flat) {
        double[] A = advOutput(adv, flat);
        double V = valOutput(val, flat);
        double m = mean(A);
        double[] Q = new double[k];
        for (int i=0;i<k;i++) Q[i] = V + (A[i] - m);
        return Q;
    }
    private double[] advOutput(MultiLayerNetwork net, double[] flat) {
        INDArray x = convFromFlat(flat, 1);
        return net.output(x, false).toDoubleVector();
    }
    private double valOutput(MultiLayerNetwork net, double[] flat) {
        INDArray x = convFromFlat(flat, 1);
        return net.output(x, false).getDouble(0);
    }

    private void hardSyncTarget() {
        targetAdv.setParams(onlineAdv.params().dup());
        targetVal.setParams(onlineVal.params().dup());
    }

    // -------- Replay / PER --------
    private void addReplay(Transition t) {
        if (t == null) return;
        while (replay.size() >= REPLAY_CAPACITY) {
            Transition old = replay.pollFirst();
            if (old != null) pri.remove(old);
        }
        replay.addLast(t);
        // New samples get max priority
        double maxP = 1.0;
        for (Double p : pri.values()) if (p != null && p > maxP) maxP = p;
        pri.put(t, maxP + 1e-3);
    }

    // -------- Reward --------
    private double rewardFromRank(double hcTrue, double reTrue, double qlrTrue) {
        double ecr = 1.0 - safeDiv(reTrue, initialEnergy);
        if (ecr < 0.0) ecr = 0.0; if (ecr > 1.0) ecr = 1.0;
        double rank = hcTrue + w1_QU * qlrTrue + w2_ECR * ecr;
        if (!Double.isFinite(rank) || rank <= 0.0) rank = 1.0;
        double r = 1.0 / rank;
        return Double.isFinite(r) ? r : 0.0;
    }

    // -------- Utilities --------
    private static double safeDiv(double num, double den) {
        if (!Double.isFinite(num) || !Double.isFinite(den) || den == 0.0) return 0.0;
        double v = num / den; return Double.isFinite(v) ? v : 0.0;
    }

    private double[] flattenState(double[][] S) {
        double[] out = new double[k * Factive];
        int idx = 0;
        for (int i=0;i<k;i++) {
            double[] row = (S != null && i < S.length) ? S[i] : null;
            for (int j=0;j<Ftotal;j++) {
                if (featureMask[j]) {
                    double v = (row != null && j < row.length) ? row[j] : 0.0;
                    out[idx++] = scaleFeature(j, v);
                }
            }
        }
        return out;
    }

    // Build conv input tensor from flat [k*Factive]
    private INDArray convFromFlat(double[] flat, int mb) {
        INDArray x = Nd4j.create(mb, 1, k, Factive);
        int idx = 0;
        for (int i=0;i<k;i++)
            for (int j=0;j<Factive;j++)
                x.putScalar(0, 0, i, j, (idx < flat.length ? flat[idx++] : 0.0));
        return x;
    }
    private void putConvFromFlat(INDArray X, int batchIdx, double[] flat) {
        int idx = 0;
        for (int i=0;i<k;i++)
            for (int j=0;j<Factive;j++)
                X.putScalar(batchIdx, 0, i, j, (idx < flat.length ? flat[idx++] : 0.0));
    }

    private static boolean[] copyOrAllValid(boolean[] valid) {
        if (valid == null) return null;
        return Arrays.copyOf(valid, valid.length);
    }

    private static double[] normalizeLen(double[] a, int n) {
        if (a != null && a.length == n) return a;
        double[] b = new double[n];
        if (a != null) System.arraycopy(a, 0, b, 0, Math.min(a.length, n));
        return b;
    }

    private static int clamp(int x, int lo, int hi) { return Math.max(lo, Math.min(hi, x)); }

    private static double valOrZero(double[] arr, int i) {
        return (arr != null && i >= 0 && i < arr.length && Double.isFinite(arr[i])) ? arr[i] : 0.0;
    }

    private static int argmaxMasked(double[] q, boolean[] valid) {
        int best = -1; double bestVal = Double.NEGATIVE_INFINITY;
        if (valid == null) { for (int i=0;i<q.length;i++) if (q[i]>bestVal){bestVal=q[i]; best=i;} return best; }
        int lim = Math.min(q.length, valid.length);
        for (int i=0;i<lim;i++) if (valid[i] && q[i] > bestVal) { bestVal = q[i]; best = i; }
        return best;
    }

    private int randomValid(boolean[] valid, int k) {
        if (valid == null) return rng.nextInt(Math.max(1, k));
        List<Integer> idxs = new ArrayList<>();
        for (int i=0;i<Math.min(k, valid.length); i++) if (valid[i]) idxs.add(i);
        if (idxs.isEmpty()) return 0;
        return idxs.get(rng.nextInt(idxs.size()));
    }

    private static double mean(double[] a) {
        if (a == null || a.length == 0) return 0.0;
        double s=0; for (double v:a) s+=v; return s/a.length;
    }

    private double scaleFeature(int featIdx, double v) {
        if (!Double.isFinite(v)) return 0.0;
        switch (featIdx) {
            case IDX_ETX:   return Math.max(1.0, Math.min(10.0, v)) / 10.0;
            case IDX_HC:    return v / 16.0;
            case IDX_RE:    return Math.max(0.0, Math.min(1.0, v / initialEnergy));
            case IDX_QLR:
            case IDX_BDI:
            case IDX_WR:    return Math.max(0.0, Math.min(1.0, v));
            case IDX_CC:
            case IDX_PC:    return Math.min(v, 10.0) / 10.0;
            case IDX_SI:    return Math.log1p(Math.max(0.0, v)) / 5.0;
            case IDX_GEN:
            case IDX_FWD:
            case IDX_QLOSS: return Math.log1p(Math.max(0.0, v)) / 10.0;
            default:        return v;
        }
    }

    private double computeRewardForPrevious(Episode prev,
                                            int[] candIds,
                                            double[] hcArr,
                                            double[] reArr,
                                            double[] qlrArr) {
        int idx = -1;
        if (candIds != null) {
            for (int i=0; i<candIds.length; i++)
                if (candIds[i] == prev.parentId) { idx = i; break; }
        }
        double hc = (idx >= 0) ? valOrZero(hcArr, idx) : prev.hcSnap;
        double re = (idx >= 0) ? valOrZero(reArr, idx) : prev.reSnap;
        double ql = (idx >= 0) ? valOrZero(qlrArr, idx) : prev.qlrSnap;
        return rewardFromRank(hc, re, ql);
    }

    // -------- Public setters (optional) --------
    public int getReplaySize() { return replay.size(); }
    public void setBatchSize(int b) { batchSize = Math.max(1, b); }
    public void setGamma(double g)   { gamma = g; }
    public void setRankWeights(double w1_QU, double w2_ECR) {
        this.w1_QU = Math.max(0.0, w1_QU); this.w2_ECR = Math.max(0.0, w2_ECR);
    }
    public void setPerAlpha(double a) { perAlpha = Math.max(0.0, Math.min(1.0, a)); }
    public void setEpsilonAnneal(double start, double end, int phases) {
        epsilonStart = Math.max(0.0, Math.min(1.0, start));
        epsilonEnd = Math.max(0.0, Math.min(1.0, end));
        epsilonAnnealPhases = Math.max(1, phases);
        // reset current to follow new schedule
        epsilon = epsilonStart + (epsilonEnd - epsilonStart) *
                Math.min(1.0, phaseCount / (double)Math.max(1, epsilonAnnealPhases));
    }
    public void setPerBetaAnneal(double start, double end, int phases) {
        betaStart = Math.max(0.0, Math.min(1.0, start));
        betaEnd = Math.max(0.0, Math.min(1.0, end));
        betaAnnealPhases = Math.max(1, phases);
        perBeta = betaStart + (betaEnd - betaStart) *
                Math.min(1.0, phaseCount / (double)Math.max(1, betaAnnealPhases));
    }
}
