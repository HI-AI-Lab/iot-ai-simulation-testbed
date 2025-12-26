package io.testbed.rl;

import org.deeplearning4j.nn.api.OptimizationAlgorithm;
import org.deeplearning4j.nn.conf.ConvolutionMode;
import org.deeplearning4j.nn.conf.ComputationGraphConfiguration;
import org.deeplearning4j.nn.conf.NeuralNetConfiguration;
import org.deeplearning4j.nn.conf.inputs.InputType;
import org.deeplearning4j.nn.conf.layers.ConvolutionLayer;
import org.deeplearning4j.nn.conf.layers.DenseLayer;
import org.deeplearning4j.nn.conf.layers.GlobalPoolingLayer;
import org.deeplearning4j.nn.conf.layers.OutputLayer;
import org.deeplearning4j.nn.conf.layers.PoolingType;
import org.deeplearning4j.nn.graph.ComputationGraph;

import org.nd4j.linalg.activations.Activation;
import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.dataset.MultiDataSet;
import org.nd4j.linalg.factory.Nd4j;
import org.nd4j.linalg.learning.config.Adam;
import org.nd4j.linalg.lossfunctions.LossFunctions;

import java.io.*;
import java.util.*;

/**
 * FINAL — Lei & Liu (2024) CNN-based Double Dueling DQN
 * Inputs: K × Factive matrix of selected RPL metrics.
 *
 * FINAL FIXED FEATURE ORDER (matches controller.js + node.c):
 *
 * 0 ETX
 * 1 RSSI
 * 2 PFI
 * 3 RE
 * 4 BDI
 * 5 QO
 * 6 QLR
 * 7 HC
 * 8 SI
 * 9 TV
 * 10 PC
 * 11 WR
 * 12 STR
 */
public class Agent implements Serializable {

    private static final String LOG_PATH = "/workspace/testbed/logs/agent.log";
    private static PrintWriter logger;

    static {
        try {
            logger = new PrintWriter(new FileWriter(LOG_PATH, true), true);
            logger.println("=== Agent started ===");
        } catch (Exception e) {
            e.printStackTrace();
            logger = new PrintWriter(System.err, true);
        }
    }

    private static void log(String msg) {
        logger.println(System.currentTimeMillis() + " " + msg);
        logger.flush();
    }

    // -------------------------------------------------------------------
    // FINAL 13 FEATURE INDICES
    // -------------------------------------------------------------------
    private static final int IDX_ETX = 0;
    private static final int IDX_RSSI = 1;
    private static final int IDX_PFI = 2;

    private static final int IDX_RE = 3;
    private static final int IDX_BDI = 4;
    private static final int IDX_QO = 5;
    private static final int IDX_QLR = 6;
    private static final int IDX_HC = 7;
    private static final int IDX_SI = 8;
    private static final int IDX_TV = 9;
    private static final int IDX_PC = 10;

    private static final int IDX_WR = 11;
    private static final int IDX_STR = 12;

    private static final int FTOTAL = 13;

    // -------------------------------------------------------------------
    public static class Counters {
        public int generated, delivered, dropped;
        public double residualEnergy;
        public int etx, hopCount, rankViolations;
    }

    private static class Episode {
        final double[] sFlat;
        final int a;
        final int parentId;
        final double hcSnap, reSnap, qlSnap;

        Episode(double[] s, int a, int pid, double hc, double re, double ql) {
            this.sFlat = s; this.a = a;
            this.parentId = pid;
            this.hcSnap = hc; this.reSnap = re; this.qlSnap = ql;
        }
    }

    private static class Transition {
        final double[] s;
        final int a;
        final double r;
        final double[] s2;
        final boolean done;
        final boolean[] valid2;

        Transition(double[] s, int a, double r, double[] s2, boolean done, boolean[] valid2) {
            this.s = s; this.a = a; this.r = r;
            this.s2 = s2; this.done = done; this.valid2 = valid2;
        }
    }

    // -------------------------------------------------------------------
    private final int k;
    private final boolean[] mask;
    private final int Factive;
    private final double initialEnergy;

    private final Map<Integer, Episode> open = new HashMap<>();

    private double epsilonStart = 0.30, epsilonEnd = 0.01;
    private int epsilonAnneal = 500;
    private double epsilon = epsilonStart;

    private double betaStart = 0.40, betaEnd = 1.00;
    private int betaAnneal = 500;
    private double perBeta = betaStart; // NOTE: not yet used in loss weighting

    private double gamma = 0.90;
    private int batchSize = 32;
    private int targetUpdate = 5;

    private int phase = 0;
    private long trainSteps = 0;

    // PER
    private static final int CAP = 1000;
    private final Deque<Transition> replay = new ArrayDeque<>(CAP);
    private final Map<Transition,Double> pri = new IdentityHashMap<>();
    private double perAlpha = 0.6;

    private double w_qlr = 1.0;
    private double w_ecr = 1.0;

    private final Random rnd = new Random(1234);

    private final ComputationGraph online;
    private final ComputationGraph target;

    private static final double HOP_NORM = 16.0;
    private static final double EPS = 1e-9;

    // -------------------------------------------------------------------
    public Agent(int k, boolean[] mask, double initialEnergy) {

        this.k = k;
        this.mask = Arrays.copyOf(mask, mask.length);
        this.initialEnergy = initialEnergy;

        int c = 0;
        for (boolean b : mask) if (b) c++;
        if (c == 0) throw new RuntimeException("Mask activates 0 features!");
        this.Factive = c;

        Nd4j.getRandom().setSeed(123);

        this.online = buildGraph(k);
        this.target = buildGraph(k);
        this.online.init();
        this.target.init();
        syncTarget();

        log("INIT Agent: K=" + k + " Factive=" + Factive);
    }

    // -------------------------------------------------------------------
    // CNN ARCHITECTURE (Lei & Liu 2024)
    // -------------------------------------------------------------------
    private ComputationGraph buildGraph(int outK) {
        ComputationGraphConfiguration conf =
            new NeuralNetConfiguration.Builder()
                .seed(123)
                .optimizationAlgo(OptimizationAlgorithm.STOCHASTIC_GRADIENT_DESCENT)
                .updater(new Adam(1e-3))
                .convolutionMode(ConvolutionMode.Same)
                .graphBuilder()
                .addInputs("input")
                .setInputTypes(InputType.convolutional(k, Factive, 1))
                .addLayer("conv1", new ConvolutionLayer.Builder(3,3)
                        .stride(1,1).nOut(16)
                        .activation(Activation.RELU).build(), "input")
                .addLayer("conv2", new ConvolutionLayer.Builder(3,1)
                        .stride(1,1).nOut(32)
                        .activation(Activation.RELU).build(), "conv1")
                .addLayer("pool", new GlobalPoolingLayer.Builder(PoolingType.AVG)
                        .build(), "conv2")
                .addLayer("dense", new DenseLayer.Builder()
                        .nOut(128).activation(Activation.RELU)
                        .build(), "pool")
                .addLayer("advFC", new DenseLayer.Builder()
                        .nOut(64).activation(Activation.RELU)
                        .build(), "dense")
                .addLayer("valFC", new DenseLayer.Builder()
                        .nOut(64).activation(Activation.RELU)
                        .build(), "dense")
                .addLayer("advOut", new OutputLayer.Builder(LossFunctions.LossFunction.MSE)
                        .nOut(outK).activation(Activation.IDENTITY)
                        .build(), "advFC")
                .addLayer("valOut", new OutputLayer.Builder(LossFunctions.LossFunction.MSE)
                        .nOut(1).activation(Activation.IDENTITY)
                        .build(), "valFC")
                .setOutputs("advOut","valOut")
                .build();

        return new ComputationGraph(conf);
    }

    private void syncTarget() { target.setParams(online.params().dup()); }

    // -------------------------------------------------------------------
    // DECISION STEP
    // -------------------------------------------------------------------
    public synchronized int decide(
            int moteId,
            double[][] S,
            boolean[] valid,
            Counters ctrs,
            int[] candIds,
            double[] hcArr,
            double[] reArr,
            double[] qlrArr)
    {
        double[] flat = flatten(S);

        // Close previous episode for this mote
        Episode prev = open.remove(moteId);
        if (prev != null) {
            double r = computeReward(prev, candIds, hcArr, reArr, qlrArr);
            addReplay(new Transition(prev.sFlat, prev.a, r, flat, false, copyValid(valid)));
        }

        double[] q = qValues(online, flat);
        int greedy = argmaxMasked(q, valid);
        int action = (rnd.nextDouble() < epsilon)
                ? randomValid(valid)
                : greedy;

		// DEBUG line:
		//logDecisionDebug(moteId, S, valid, candIds, hcArr, reArr, qlrArr, q, action);

        int pid = 0;
        double hc = 0, re = 0, ql = 0;

        if (action >= 0 && action < candIds.length) {
            pid = candIds[action];
            hc = safe(hcArr, action);
            re = safe(reArr, action);
            ql = safe(qlrArr, action);
        }

        open.put(moteId, new Episode(flat, action, pid, hc, re, ql));
        return action;
    }

    // -------------------------------------------------------------------
    // END PHASE
    // -------------------------------------------------------------------
    public synchronized void endPhase() {

        for (Episode ep : open.values()) {
            double r = reward(ep.hcSnap, ep.reSnap, ep.qlSnap);
            addReplay(new Transition(ep.sFlat, ep.a, r, ep.sFlat, true, null));
        }
        open.clear();

        if (!replay.isEmpty()) trainOneBatch();

        phase++;

        epsilon = epsilonStart + (epsilonEnd - epsilonStart)
                * Math.min(1.0, (double) phase / epsilonAnneal);

        perBeta = betaStart + (betaEnd - betaStart)
                * Math.min(1.0, (double) phase / betaAnneal);
    }

    // -------------------------------------------------------------------
    // TRAINING STEP (PER sampling, no IS weighting yet)
    // -------------------------------------------------------------------
    private void trainOneBatch() {

        int D = replay.size();
        int bs = Math.min(batchSize, D);
        if (bs <= 0) return;

        Transition[] pool = replay.toArray(new Transition[0]);

        double[] p = new double[D];
        double c = 0;
        for (int i = 0; i < D; i++) {
            double w = pri.getOrDefault(pool[i], 1.0);
            w = Math.pow(w, perAlpha);
            p[i] = w;
            c += w;
        }

        double[] pref = new double[D];
        double acc = 0;
        for (int i=0;i<D;i++){ acc += p[i]; pref[i] = acc; }

        int[] idx = new int[bs];
        for (int i=0;i<bs;i++) {
            double u = ((i + rnd.nextDouble()) / bs) * acc;
            idx[i] = lowerBound(pref, u);
        }

        INDArray X    = Nd4j.create(bs, 1, k, Factive);
        INDArray Yadv = Nd4j.create(bs, k);
        INDArray Yval = Nd4j.create(bs, 1);

        Transition[] batch = new Transition[bs];
        double[] newP = new double[bs];

        for (int i=0;i<bs;i++) {

            Transition t = pool[idx[i]];
            batch[i] = t;

            putConv(X, i, t.s);

            INDArray[] out = online.output(false, conv(t.s));
            double[] A = out[0].toDoubleVector();
            double V  = out[1].getDouble(0);

            double meanA = mean(A);

            double targetQ;
            if (t.done) {
                targetQ = t.r;
            } else {
                double[] Qn = qValues(online, t.s2);
                int aStar = argmaxMasked(Qn, t.valid2);

                INDArray[] outT = target.output(false, conv(t.s2));
                double[] At = outT[0].toDoubleVector();
                double Vt = outT[1].getDouble(0);
                double meanAt = mean(At);

                double Qnext = Vt + (At[aStar] - meanAt);
                targetQ = t.r + gamma * Qnext;
            }

            int a = t.a;
            double Qcurr = V + (A[a] - meanA);
            double td = targetQ - Qcurr;

            newP[i] = Math.abs(td) + 1e-3;

            double[] A_lbl = Arrays.copyOf(A, k);
            A_lbl[a] = targetQ - V + meanA;
            Yadv.getRow(i).assign(Nd4j.create(A_lbl));

            double V_lbl = targetQ - (A[a] - meanA);
            Yval.putScalar(i, 0, V_lbl);
        }

        MultiDataSet mds = new MultiDataSet(
                new INDArray[]{X}, new INDArray[]{Yadv, Yval});
        online.fit(mds);

        trainSteps++;
        if (trainSteps % targetUpdate == 0) syncTarget();

        for (int i = 0; i < bs; i++)
            pri.put(batch[i], newP[i]);
    }

    // -------------------------------------------------------------------
    private double[] qValues(ComputationGraph net, double[] flat) {
        INDArray x = conv(flat);
        INDArray[] out = net.output(false, x);

        double[] A = out[0].toDoubleVector();
        double V   = out[1].getDouble(0);
        double m   = mean(A);

        double[] Q = new double[k];
        for (int i=0;i<k;i++) Q[i] = V + (A[i] - m);

        return Q;
    }

    // -------------------------------------------------------------------
    // STATE FLATTENING WITH FIXED SCALING (K rows, zero-padded)
    // -------------------------------------------------------------------
    private double[] flatten(double[][] S) {

        double[] out = new double[k * Factive];
        int p = 0;

        for (int i = 0; i < k; i++) {

            double[] row = (S != null && i < S.length) ? S[i] : null;
            int pos = 0;

            for (int f = 0; f < FTOTAL; f++) {
                if (mask[f]) {
                    double v = (row != null && pos < row.length) ? row[pos++] : 0.0;
                    out[p++] = scale(f, v);
                }
            }
        }
        return out;
    }

    // -------------------------------------------------------------------
    private INDArray conv(double[] flat) {
        INDArray x = Nd4j.create(1,1,k,Factive);
        int idx=0;
        for (int i=0;i<k;i++)
            for (int j=0;j<Factive;j++)
                x.putScalar(0,0,i,j, flat[idx++]);
        return x;
    }

    private void putConv(INDArray X, int row, double[] flat) {
        int idx=0;
        for (int i=0;i<k;i++)
            for (int j=0;j<Factive;j++)
                X.putScalar(row,0,i,j, flat[idx++]);
    }

    // -------------------------------------------------------------------
    private void addReplay(Transition t) {
        if (replay.size() >= CAP) {
            Transition old = replay.pollFirst();
            pri.remove(old);
        }
        replay.addLast(t);

        double mx = pri.values().stream()
                .mapToDouble(v -> v)
                .max().orElse(1.0);

        pri.put(t, mx + 1e-3);
    }

    // -------------------------------------------------------------------
	
	// -------------------------------------------------------------------
	// DEBUG: log what the agent sees and does
	// -------------------------------------------------------------------
	private void logDecisionDebug(
			int moteId,
			double[][] S,
			boolean[] valid,
			int[] candIds,
			double[] hcArr,
			double[] reArr,
			double[] qlrArr,
			double[] qValues,
			int action)
	{
		// Only log a subset to avoid GB-sized logs
		if (moteId > 5) return;          // only first few motes
		if (phase > 20) return;          // only early phases
		if (rnd.nextDouble() > 0.05) return;  // 5% sampling

		StringBuilder sb = new StringBuilder();
		sb.append("DECISION ");
		sb.append("phase=").append(phase)
		  .append(" mote=").append(moteId)
		  .append(" eps=").append(String.format("%.3f", epsilon))
		  .append(" cand=");

		sb.append(Arrays.toString(candIds));
		sb.append(" valid=").append(Arrays.toString(valid)).append("\n");

		// Features per candidate row
		for (int i = 0; i < candIds.length; i++) {
			sb.append("  cand[").append(i).append("] id=").append(candIds[i]);

			if (S != null && i < S.length) {
				sb.append(" features=").append(Arrays.toString(S[i]));
			} else {
				sb.append(" features=[]");
			}

			double hc = (hcArr != null && i < hcArr.length) ? hcArr[i] : 0.0;
			double re = (reArr != null && i < reArr.length) ? reArr[i] : 0.0;
			double ql = (qlrArr != null && i < qlrArr.length) ? qlrArr[i] : 0.0;

			sb.append(" hc=").append(String.format("%.2f", hc))
			  .append(" re=").append(String.format("%.1f", re))
			  .append(" qlr=").append(String.format("%.3f", ql));

			sb.append("\n");
		}

		sb.append("  qValues=").append(Arrays.toString(qValues))
		  .append("  action=").append(action);

		if (action >= 0 && action < candIds.length) {
			sb.append("  chosenParent=").append(candIds[action]);
		}

		log(sb.toString());
	}
    
	private double reward(double hc, double re, double qlr) {

        double ecr = 1.0 - Math.max(0.0, Math.min(1.0, re / initialEnergy));
        double hcN = Math.max(0.0, Math.min(1.0, hc / HOP_NORM));
        double q = Math.max(0.0, Math.min(1.0, qlr));

        double R = hcN + w_qlr*q + w_ecr*ecr;
        return 1.0 / (1.0 + R + EPS);
    }
	
	// DEBUG REWARD: make the agent love *bad* links
	/*
	private double reward(double hc, double re, double qlr) {

		// same normalisations as before
		double ecr = 1.0 - Math.max(0.0, Math.min(1.0, re / initialEnergy));
		double hcN = Math.max(0.0, Math.min(1.0, hc / HOP_NORM));
		double q   = Math.max(0.0, Math.min(1.0, qlr));

		// "badness" score: high hop-count, high loss, high energy
		double Rbad = hcN + q + ecr;

		// IMPORTANT: now we directly return Rbad,
		// so higher "badness" means higher reward
		return Rbad;
	}
	*/
    private double computeReward(Episode ep,
                                 int[] candIds,
                                 double[] hcArr, double[] reArr, double[] qlrArr)
    {
        int idx = -1;
        for (int i=0;i<candIds.length;i++)
            if (candIds[i] == ep.parentId) idx = i;

        double hc = (idx>=0) ? safe(hcArr,idx) : ep.hcSnap;
        double re = (idx>=0) ? safe(reArr,idx) : ep.reSnap;
        double ql = (idx>=0) ? safe(qlrArr,idx) : ep.qlSnap;

        return reward(hc,re,ql);
    }

    // -------------------------------------------------------------------
    // FINAL CORRECT SCALING
    // -------------------------------------------------------------------
    private double scale(int f, double v) {

        if (!Double.isFinite(v)) return 0.0;

        switch(f){

            // ---------------- LINK ----------------
            case IDX_ETX:  return Math.min(Math.max(v,0.0),10.0) / 10.0;
            case IDX_RSSI: return Math.min(Math.max(v,0.0),10.0) / 10.0;

            // PFI in [0,1]
            case IDX_PFI:  return Math.max(0.0, Math.min(1.0, v));

            // ---------------- NODE ----------------
            case IDX_RE:   return Math.max(0.0, Math.min(1.0, v / initialEnergy));
            case IDX_BDI:  return Math.max(0.0, Math.min(1.0, v));

            // QO must be normalized (QUEUEBUF=8)
            case IDX_QO:   return Math.max(0.0, Math.min(1.0, v / 8.0));

            case IDX_QLR:  return Math.max(0.0, Math.min(1.0, v));
            case IDX_HC:   return Math.max(0.0, Math.min(1.0, v / HOP_NORM));

            // SI and TV already ∈ [0,1]
            case IDX_SI:   return Math.max(0.0, Math.min(1.0, v));
            case IDX_TV:   return Math.max(0.0, Math.min(1.0, v));

            // For safety limit PC to [0,10]
            case IDX_PC:   return Math.min(Math.max(v,0.0),10.0) / 10.0;

            // ---------------- NETWORK ----------------
            case IDX_WR:   return Math.max(0.0, Math.min(1.0, v));

            // STR may be large
            case IDX_STR:  return Math.log1p(Math.max(0.0, v)) / 5.0;
        }

        return 0.0;
    }

    // -------------------------------------------------------------------
    private static double safe(double[] arr, int i){
        return (arr!=null && i>=0 && i<arr.length && Double.isFinite(arr[i]))
                ? arr[i] : 0.0;
    }

    private static boolean[] copyValid(boolean[] v){
        return v==null?null:Arrays.copyOf(v,v.length);
    }

    // FIX: robust to valid.length < k
    private static int argmaxMasked(double[] q, boolean[] valid){
        int b=-1;
        double bv=Double.NEGATIVE_INFINITY;
        for (int i=0;i<q.length;i++){
            boolean ok = (valid == null) || (i < valid.length && valid[i]);
            if (ok){
                if (q[i]>bv){ bv=q[i]; b=i; }
            }
        }
        return (b<0)?0:b;
    }

    // FIX: robust to valid.length < k
    private int randomValid(boolean[] valid){
        if (valid == null) {
            return rnd.nextInt(k);
        }
        List<Integer> L = new ArrayList<>();
        for (int i=0;i<k;i++) {
            if (i < valid.length && valid[i]) {
                L.add(i);
            }
        }
        return L.isEmpty() ? 0 : L.get(rnd.nextInt(L.size()));
    }

    private static int lowerBound(double[] a, double x){
        int lo=0, hi=a.length-1;
        while (lo<hi) {
            int m=(lo+hi)>>>1;
            if (a[m]>=x) hi=m; else lo=m+1;
        }
        return lo;
    }

    private static double mean(double[] a){
        double s=0; int n=0;
        for (double v:a) if (Double.isFinite(v)){ s+=v; n++; }
        return n>0? (s/n):0.0;
    }
}
