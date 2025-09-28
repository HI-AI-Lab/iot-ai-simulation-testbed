package io.testbed.rl;

import org.deeplearning4j.nn.api.OptimizationAlgorithm;
import org.deeplearning4j.nn.conf.MultiLayerConfiguration;
import org.deeplearning4j.nn.conf.NeuralNetConfiguration;
import org.deeplearning4j.nn.conf.layers.DenseLayer;
import org.deeplearning4j.nn.conf.layers.OutputLayer;
import org.deeplearning4j.nn.multilayer.MultiLayerNetwork;
import org.nd4j.linalg.activations.Activation;
import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.factory.Nd4j;
import org.nd4j.linalg.learning.config.Adam;
import org.nd4j.linalg.lossfunctions.LossFunctions;

import java.io.Serializable;
import java.util.*;

/**
 * Double-DQN Agent (CTDE-style, parameterless endPhase).
 * - Children call decide(); sink calls endPhase() to close episodes & train.
 * - Reward matches Lei & Liu (2024): r = 1/Rank(ni) for valid action, -1 for invalid.
 *   Rank(ni) = Rank(p) + w1*QU(p) + w2*ECR(p).
 *   We use QLR as QU proxy by default; set useTrueQU if you expose QU.
 */
public class Agent implements Serializable {

    // -------- Controller inputs --------
    public static class Counters {
        // Kept for compatibility; not used by rank-based reward.
        public int generated, delivered, dropped;
        public double residualEnergy; // child-side (unused in reward)
        public int etx, hopCount, rankViolations;
    }

    // -------- Episode & Replay --------
    private static class Episode {
        final double[] sFlat; // k*Factive (row-major)
        final int a;          // chosen index in [0..k-1]
        Episode(double[] sFlat, int a) { this.sFlat = sFlat; this.a = a; }
    }

    private static class Transition {
        final double[] s; final int a; final double r;
        final double[] s2; final boolean done; final boolean[] valid2;
        Transition(double[] s, int a, double r, double[] s2, boolean done, boolean[] valid2) {
            this.s = s; this.a = a; this.r = r; this.s2 = s2; this.done = done; this.valid2 = valid2;
        }
    }

    // -------- Hyperparams --------
    private final int k;                 // max candidate parents (actions)
    private final int Ftotal;            // total features defined
    private final boolean[] featureMask; // active features
    private final int Factive;           // active count per parent

    private double epsilon = 0.10;
    private double gamma   = 0.90;
    private int    batchSize = 32;
    private int    targetUpdateEvery = 5;

    // Rank reward weights (Rank = HC + w1*QU + w2*ECR)
    private double w1_QU = 1.0;
    private double w2_ECR = 1.0;

    // If you expose real QU later, set this to true; by default we use QLR as a QU proxy.
    private boolean useTrueQU = false;

    // Energy baseline for ECR = 1 - RE/INIT
    private final double initialEnergy;

    // -------- State --------
    private final Map<Integer, Episode> open = new HashMap<>();
    private static final int REPLAY_CAPACITY = 100_000;
    private final Deque<Transition> replay = new ArrayDeque<>(REPLAY_CAPACITY);

    private final Random rng = new Random(1234);
    private final MultiLayerNetwork online;
    private final MultiLayerNetwork target;
    private long trainSteps = 0;

    // Map of feature index -> position within the active feature vector (0..Factive-1)
    // So we can recover Rank(p), QLR/QU(p), RE(p) from a stored sFlat row.
    private final Map<Integer, Integer> activePos = new HashMap<>();

    // Constants to name features by index in mask (for clarity)
    private static final int IDX_ETX  = 0;  // not used in Rank()
    private static final int IDX_HC   = 1;  // Rank(p)
    private static final int IDX_RE   = 2;  // Residual Energy (for ECR)
    private static final int IDX_QLR  = 3;  // Proxy for QU if useTrueQU==false
    private static final int IDX_BDI  = 4;  // unused here
    private static final int IDX_WR   = 5;  // unused here
    private static final int IDX_CC   = 6;  // unused here
    private static final int IDX_PC   = 7;  // unused here
    private static final int IDX_SI   = 8;  // unused here
    private static final int IDX_GEN  = 9;  // unused here
    private static final int IDX_FWD  = 10; // unused here
    private static final int IDX_QLOSS= 11; // unused here

    // -------- Construction --------
    /**
     * @param k    max candidate parents (actions)
     * @param mask feature mask (length Ftotal), must include at least HC and RE.
     * @param initialEnergyJ initial energy (same unit as RE in state) to compute ECR.
     */
    public Agent(int k, boolean[] mask, double initialEnergyJ) {
        if (k <= 0) throw new IllegalArgumentException("k must be > 0");
        if (mask == null || mask.length == 0) throw new IllegalArgumentException("mask must be non-empty");

        this.k = k;
        this.Ftotal = mask.length;
        this.featureMask = Arrays.copyOf(mask, mask.length);
        this.initialEnergy = initialEnergyJ > 0 ? initialEnergyJ : 1.0; // avoid div-by-zero

        int cnt = 0;
        for (int j = 0; j < mask.length; j++) {
            if (mask[j]) { activePos.put(j, cnt); cnt++; }
        }
        if (cnt == 0) throw new IllegalArgumentException("featureMask has no active features");
        if (!activePos.containsKey(IDX_HC) || !activePos.containsKey(IDX_RE)) {
            throw new IllegalArgumentException("Mask must include HC (idx 1) and RE (idx 2) for rank reward.");
        }
        this.Factive = cnt;

        int in = k * Factive;
        Nd4j.getRandom().setSeed(123);

        MultiLayerConfiguration conf = new NeuralNetConfiguration.Builder()
                .seed(123)
                .optimizationAlgo(OptimizationAlgorithm.STOCHASTIC_GRADIENT_DESCENT)
                .updater(new Adam(1e-3))
                .weightInit(org.deeplearning4j.nn.weights.WeightInit.XAVIER)
                .list()
                .layer(new DenseLayer.Builder().nIn(in).nOut(64).activation(Activation.RELU).build())
                .layer(new DenseLayer.Builder().nIn(64).nOut(128).activation(Activation.RELU).build())
                .layer(new DenseLayer.Builder().nIn(128).nOut(64).activation(Activation.RELU).build())
                .layer(new OutputLayer.Builder(LossFunctions.LossFunction.MSE)
                        .activation(Activation.IDENTITY)
                        .nIn(64).nOut(k).build())
                .build();

        this.online = new MultiLayerNetwork(conf);
        this.online.init();

        this.target = new MultiLayerNetwork(conf.clone());
        this.target.init();
        hardSyncTarget();
    }

    // -------- Public API (Controller) --------

    /** Children call: choose action & open episode. No training here. */
    public synchronized int decide(int moteId, double[][] S, boolean[] valid, Counters countersNow) {
        double[] flat = flattenState(S);

        // Close previous episode for this mote: reward by rank-based rule (paper).
        Episode prev = open.remove(moteId);
        if (prev != null) {
            double rPrev = rewardFromRank(prev.sFlat, prev.a);
            // Next-state is current flat; not terminal yet.
            addReplay(new Transition(prev.sFlat, prev.a, rPrev, flat, false, copyOrAllValid(valid)));
        }

        // Select next action
        int a = selectAction(flat, valid);
        open.put(moteId, new Episode(flat, a));
        return a;
    }

    /** Sink calls: finalize all open episodes and train (CTDE). Parameterless. */
    public synchronized void endPhase() {
        // Close all open episodes using rank-based reward; terminal transitions.
        for (Map.Entry<Integer, Episode> e : open.entrySet()) {
            Episode ep = e.getValue();
            double r = rewardFromRank(ep.sFlat, ep.a);
            addReplay(new Transition(ep.sFlat, ep.a, r, ep.sFlat, true, null));
        }
        open.clear();

        // One full training round over the current buffer
        trainStep(autoBatches());
    }

    // -------- Training (Double-DQN) --------

    private void trainStep(int nBatches) {
        if (nBatches <= 0 || replay.isEmpty()) return;

        for (int b = 0; b < nBatches; b++) {
            List<Transition> batch = sampleBatch(batchSize);
            if (batch.isEmpty()) break;

            int bs = batch.size();
            INDArray X = Nd4j.create(bs, k * Factive);
            INDArray Y = Nd4j.create(bs, k);

            for (int i = 0; i < bs; i++) {
                Transition t = batch.get(i);
                double[] s = normalizeLen(t.s, k * Factive);
                double[] s2 = normalizeLen(t.s2, k * Factive);

                X.putRow(i, Nd4j.createFromArray(s));

                double[] qCurr = forward(online, s);
                double targetQ;
                if (t.done) {
                    targetQ = t.r;
                } else {
                    int aStar = argmaxMasked(forward(online, s2), t.valid2);
                    targetQ = (aStar < 0) ? t.r : t.r + gamma * forward(target, s2)[aStar];
                }
                if (!Double.isFinite(targetQ)) targetQ = 0.0;

                int aIdx = clamp(t.a, 0, k - 1);
                if (!Double.isFinite(qCurr[aIdx])) qCurr[aIdx] = 0.0;
                qCurr[aIdx] = targetQ;

                Y.putRow(i, Nd4j.createFromArray(qCurr));
            }

            online.fit(X, Y);
            trainSteps++;
            if (trainSteps % Math.max(1, targetUpdateEvery) == 0) hardSyncTarget();
        }
    }

    private List<Transition> sampleBatch(int n) {
        int sz = replay.size();
        if (sz == 0) return Collections.emptyList();
        n = Math.min(n, sz);
        ArrayList<Transition> list = new ArrayList<>(replay);
        Collections.shuffle(list, rng);
        return list.subList(0, n);
    }

    // -------- Q-net helpers --------

    private double[] forward(MultiLayerNetwork net, double[] flatState) {
        double[] fs = normalizeLen(flatState, k * Factive);
        INDArray out = net.output(Nd4j.createFromArray(fs).reshape(1, k * Factive), false);
        return out.toDoubleVector();
    }

    private int selectAction(double[] flat, boolean[] valid) {
        double[] q = forward(online, flat);
        int greedy = argmaxMasked(q, valid);
        if (greedy < 0) greedy = 0;

        if (rng.nextDouble() < epsilon) {
            List<Integer> idxs = new ArrayList<>();
            if (valid == null) {
                for (int i = 0; i < k; i++) idxs.add(i);
            } else {
                for (int i = 0; i < Math.min(k, valid.length); i++)
                    if (valid[i]) idxs.add(i);
            }
            if (!idxs.isEmpty()) return idxs.get(rng.nextInt(idxs.size()));
        }
        return greedy;
    }

    private static int argmaxMasked(double[] q, boolean[] valid) {
        int best = -1; double bestVal = Double.NEGATIVE_INFINITY;
        if (valid == null) {
            for (int i = 0; i < q.length; i++) if (q[i] > bestVal) { bestVal = q[i]; best = i; }
            return best;
        }
        int lim = Math.min(q.length, valid.length);
        for (int i = 0; i < lim; i++) {
            if (valid[i] && q[i] > bestVal) { bestVal = q[i]; best = i; }
        }
        return best;
    }

    private void hardSyncTarget() { target.setParams(online.params().dup()); }

    // -------- Rank-based reward (paper) --------

    /**
     * Compute r = 1/Rank(ni) if chosen slot is a real parent; else r = -1.
     * Rank(ni) = Rank(p) + w1*QU(p) + w2*ECR(p).
     *   - Rank(p): parent's HC (feature idx 1).
     *   - QU(p): if useTrueQU==true, expects a QU feature in state (not currently present);
     *            else uses QLR (feature idx 3) as a proxy, consistent with paper’s heavy-traffic congestion signals.
     *   - ECR(p): 1 - RE(p)/initialEnergy, where RE(p) is parent residual energy (feature idx 2).
     */
    private double rewardFromRank(double[] sFlat, int actionIdx) {
        if (sFlat == null) return -1.0;
        int rowStart = actionIdx * Factive;
        if (rowStart < 0 || rowStart + Factive > sFlat.length) return -1.0;

        // Detect zero-padded (invalid) row: all zeros across active features
        boolean allZero = true;
        for (int d = 0; d < Factive; d++) {
            if (sFlat[rowStart + d] != 0.0) { allZero = false; break; }
        }
        if (allZero) return -1.0;

        // Recover needed parent features from active slots
        double hc = getActive(sFlat, rowStart, IDX_HC, 0.0);
        double re = getActive(sFlat, rowStart, IDX_RE, initialEnergy); // fallback to INIT if missing
        double congestion = useTrueQU
                ? getActive(sFlat, rowStart, /*QU idx*/ IDX_QLR, 0.0) // placeholder if you later map QU
                : getActive(sFlat, rowStart, IDX_QLR, 0.0);           // QLR proxy

        double ecr = 1.0 - safeDiv(re, initialEnergy);
        if (ecr < 0.0) ecr = 0.0;
        if (ecr > 1.0) ecr = 1.0;

        double rank = hc + w1_QU * congestion + w2_ECR * ecr;
        if (!Double.isFinite(rank) || rank <= 0.0) rank = 1.0; // avoid div-by-zero or NaN
        double r = 1.0 / rank;
        if (!Double.isFinite(r)) r = 0.0;
        return r;
    }

    private double getActive(double[] flat, int rowStart, int featIdx, double defVal) {
        Integer pos = activePos.get(featIdx);
        if (pos == null) return defVal; // feature not active
        double v = flat[rowStart + pos];
        return Double.isFinite(v) ? v : defVal;
        }
    // -------- Utilities --------

    private double[] flattenState(double[][] S) {
        double[] out = new double[k * Factive];
        int idx = 0;
        for (int i = 0; i < k; i++) {
            double[] row = (S != null && i < S.length) ? S[i] : null;
            for (int j = 0; j < Ftotal; j++) {
                if (featureMask[j]) {
                    double v = (row != null && j < row.length) ? row[j] : 0.0;
                    out[idx++] = Double.isFinite(v) ? v : 0.0;
                }
            }
        }
        return out;
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

    private static int clamp(int x, int lo, int hi) {
        return Math.max(lo, Math.min(hi, x));
    }

    private int autoBatches() {
        int D = replay.size();
        if (D == 0) return 0;
        return (int) Math.ceil(D / (double) Math.max(1, batchSize));
    }

    // -------- Tuning --------
    public int getReplaySize() { return replay.size(); }
    public int getBatchSize()  { return batchSize; }
    public void setBatchSize(int b) { batchSize = Math.max(1, b); }
    public void setGamma(double g)   { gamma = g; }
    public void setEpsilon(double e) { epsilon = Math.max(0.0, Math.min(1.0, e)); }
    public void setTargetUpdateEvery(int c) { targetUpdateEvery = Math.max(1, c); }

    /** Adjust reward weights (paper uses QU & ECR terms in Rank). */
    public void setRankWeights(double w1_QU, double w2_ECR) {
        this.w1_QU = Math.max(0.0, w1_QU);
        this.w2_ECR = Math.max(0.0, w2_ECR);
    }

    /** Switch to true when you expose a QU feature in state; default uses QLR as proxy. */
    public void setUseTrueQU(boolean useTrueQU) { this.useTrueQU = useTrueQU; }
}
