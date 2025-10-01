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

import java.io.FileWriter;
import java.io.IOException;
import java.io.PrintWriter;

/**
 * Double-DQN Agent (CTDE-style).
 * - Children call decide(); sink calls endPhase() to close episodes & train.
 * - NN input is ablation-sensitive via feature mask.
 * - Reward always uses true HC, RE, QLR (mask-independent).
 */
public class Agent implements Serializable {

	private static final String LOG_PATH = "/workspace/testbed/logs/agent.log";
	private static PrintWriter logger;

	static {
		try {
			logger = new PrintWriter(new FileWriter(LOG_PATH, true), true); // append mode
			logger.println("=== Agent started ===");
		} catch (IOException e) {
			e.printStackTrace();
			logger = new PrintWriter(System.err, true);
		}
	}

	private static void log(String msg) {
		if (logger != null) {
			logger.println(System.currentTimeMillis() + " " + msg);
			logger.flush();
		}
	}

    // -------- Controller inputs --------
    public static class Counters {
        public int generated, delivered, dropped;
        public double residualEnergy;
        public int etx, hopCount, rankViolations;
    }

    // -------- Episode & Replay --------
	private static class Episode {
		final double[] sFlat;
		final int a;          // action index (0..k-1)
		final int parentId;   // actual chosen parent ID
		final double hcSnap, reSnap, qlrSnap; // fallback snapshots

		Episode(double[] sFlat, int a, int parentId,
				double hcSnap, double reSnap, double qlrSnap) {
			this.sFlat = sFlat;
			this.a = a;
			this.parentId = parentId;
			this.hcSnap = hcSnap;
			this.reSnap = reSnap;
			this.qlrSnap = qlrSnap;
		}
	}

    private static class Transition {
        final double[] s; final int a; final double r;
        final double[] s2; final boolean done; final boolean[] valid2;
        Transition(double[] s, int a, double r, double[] s2, boolean done, boolean[] valid2) {
            this.s = s; this.a = a; this.r = r; this.s2 = s2; this.done = done; this.valid2 = valid2;
        }
    }

    // -------- Feature constants (for ablation clarity) --------
    private static final int IDX_ETX   = 0;
    private static final int IDX_HC    = 1;
    private static final int IDX_RE    = 2;
    private static final int IDX_QLR   = 3;
    private static final int IDX_BDI   = 4;
    private static final int IDX_WR    = 5;
    private static final int IDX_CC    = 6;
    private static final int IDX_PC    = 7;
    private static final int IDX_SI    = 8;
    private static final int IDX_GEN   = 9;
    private static final int IDX_FWD   = 10;
    private static final int IDX_QLOSS = 11;

    // -------- Hyperparams --------
    private final int k;
    private final int Ftotal;
    private final boolean[] featureMask;
    private final int Factive;

    private double epsilon = 0.30;
    private double gamma   = 0.90;
    private int batchSize = 32;
    private int targetUpdateEvery = 5;

    private double w1_QU = 1.0;
    private double w2_ECR = 1.0;

    private final double initialEnergy;

    // -------- State --------
    private final Map<Integer, Episode> open = new HashMap<>();
    private static final int REPLAY_CAPACITY = 100_000;
    private final Deque<Transition> replay = new ArrayDeque<>(REPLAY_CAPACITY);

    private final Random rng = new Random(1234);
    private final MultiLayerNetwork online;
    private final MultiLayerNetwork target;
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
        for (int j = 0; j < mask.length; j++) {
            if (mask[j]) { activePos.put(j, cnt); cnt++; }
        }
        if (cnt == 0) throw new IllegalArgumentException("featureMask has no active features");
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

	public synchronized int decide(int moteId,
								   double[][] S,
								   boolean[] valid,
								   Counters countersNow,
								   int[] candIds,
								   double[] hcArr,
								   double[] reArr,
								   double[] qlrArr) {
		double[] flat = flattenState(S);

		// Close previous episode
		Episode prev = open.remove(moteId);
		if (prev != null) {
			double rPrev = computeRewardForPrevious(prev, candIds, hcArr, reArr, qlrArr);
			addReplay(new Transition(prev.sFlat, prev.a, rPrev, flat, false, copyOrAllValid(valid)));
		}

		// Choose new action
		int a = selectAction(flat, valid);

		// Defensive index checks
		int chosenParentId = 0;
		double hcSnap = 0.0, reSnap = 0.0, qlSnap = 0.0;
		if (a >= 0 && candIds != null && a < candIds.length) {
			chosenParentId = candIds[a];
			hcSnap = valOrZero(hcArr, a);
			reSnap = valOrZero(reArr, a);
			qlSnap = valOrZero(qlrArr, a);
		}

		// Store episode with chosen parent + snapshots
		open.put(moteId, new Episode(flat, a, chosenParentId, hcSnap, reSnap, qlSnap));
		/*
		log("decide: mote=" + moteId +
			" choiceIdx=" + a +
			" parentId=" + chosenParentId +
			" hc=" + hcSnap +
			" re=" + reSnap +
			" qlr=" + qlSnap +
			" eps=" + epsilon);*/
		return a;
	}

	public synchronized void endPhase() {
		
		double sumR = 0.0;
		double minR = Double.POSITIVE_INFINITY;
		double maxR = Double.NEGATIVE_INFINITY;
		int countR = 0;

		for (Map.Entry<Integer, Episode> e : open.entrySet()) {
			Episode ep = e.getValue();
			// Use stored snapshot values
			double r = rewardFromRank(ep.hcSnap, ep.reSnap, ep.qlrSnap);
			addReplay(new Transition(ep.sFlat, ep.a, r, ep.sFlat, true, null));

			sumR += r;
			minR = Math.min(minR, r);
			maxR = Math.max(maxR, r);
			countR++;
		}
		open.clear();

		int nBatches = autoBatches();
		trainStep(nBatches);

		// epsilon decay
		epsilon = Math.max(0.01, epsilon * 0.995);

		double avgR = (countR > 0) ? sumR / countR : 0.0;
		/*
		log("endPhase: replay=" + replay.size() +
			" batches=" + nBatches +
			" avgR=" + avgR +
			" minR=" + minR +
			" maxR=" + maxR +
			" epsilon=" + epsilon);*/
	}

    // -------- Training --------
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

    // -------- Reward (mask-independent) --------
    private double rewardFromRank(double hcTrue, double reTrue, double qlrTrue) {
        double ecr = 1.0 - safeDiv(reTrue, initialEnergy);
        if (ecr < 0.0) ecr = 0.0;
        if (ecr > 1.0) ecr = 1.0;

        double rank = hcTrue + w1_QU * qlrTrue + w2_ECR * ecr;
        if (!Double.isFinite(rank) || rank <= 0.0) rank = 1.0;
        double r = 1.0 / rank;
        if (!Double.isFinite(r)) r = 0.0;
            /*log("reward: hc=" + hcTrue + " re=" + reTrue + " qlr=" + qlrTrue +
				" ecr=" + ecr + " => r=" + r);*/
		return r;
    }

    // -------- Utilities --------
    private void addReplay(Transition t) {
        if (t == null) return;
        while (replay.size() >= REPLAY_CAPACITY) {
            replay.pollFirst();
        }
        replay.addLast(t);
    }

    private static double safeDiv(double num, double den) {
        if (!Double.isFinite(num) || !Double.isFinite(den) || den == 0.0) return 0.0;
        double v = num / den;
        return Double.isFinite(v) ? v : 0.0;
    }

    private double[] flattenState(double[][] S) {
        double[] out = new double[k * Factive];
        int idx = 0;
        for (int i = 0; i < k; i++) {
            double[] row = (S != null && i < S.length) ? S[i] : null;
            for (int j = 0; j < Ftotal; j++) {
                if (featureMask[j]) {
                    double v = (row != null && j < row.length) ? row[j] : 0.0;
                    out[idx++] = scaleFeature(j, v);
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
    public void setBatchSize(int b) { batchSize = Math.max(1, b); }
    public void setGamma(double g)   { gamma = g; }
    public void setEpsilon(double e) { epsilon = Math.max(0.0, Math.min(1.0, e)); }
    public void setTargetUpdateEvery(int c) { targetUpdateEvery = Math.max(1, c); }
    public void setRankWeights(double w1_QU, double w2_ECR) {
        this.w1_QU = Math.max(0.0, w1_QU);
        this.w2_ECR = Math.max(0.0, w2_ECR);
    }
	
	private double scaleFeature(int featIdx, double v) {
		if (!Double.isFinite(v)) return 0.0;
		switch (featIdx) {
			case IDX_ETX:
				return Math.max(1.0, Math.min(10.0, v)) / 10.0; // ~[0.1,1]
			case IDX_HC:
				return v / 16.0; // hop count ~[0,1]
			case IDX_RE:
				return Math.max(0.0, Math.min(1.0, v / initialEnergy));
			case IDX_QLR:
			case IDX_BDI:
			case IDX_WR:
				return Math.max(0.0, Math.min(1.0, v)); // ratios already 0–1
			case IDX_CC:
				return Math.min(v, 10.0) / 10.0; // child count, assume ≤10
			case IDX_PC:
				return Math.min(v, 10.0) / 10.0; // parent count, assume ≤10
			case IDX_SI:
				return Math.log1p(Math.max(0.0, v)) / 5.0; // parent switches, compress
			case IDX_GEN:
			case IDX_FWD:
			case IDX_QLOSS:
				return Math.log1p(Math.max(0.0, v)) / 10.0; // counters
				default:
				return v;
		}
	}
	
	private double computeRewardForPrevious(Episode prev,
											int[] candIds,
											double[] hcArr,
											double[] reArr,
											double[] qlrArr) {
		int idx = -1;
		if (candIds != null) {
			for (int i = 0; i < candIds.length; i++) {
				if (candIds[i] == prev.parentId) { idx = i; break; }
			}
		}
		double hc = (idx >= 0) ? valOrZero(hcArr, idx) : prev.hcSnap;
		double re = (idx >= 0) ? valOrZero(reArr, idx) : prev.reSnap;
		double ql = (idx >= 0) ? valOrZero(qlrArr, idx) : prev.qlrSnap;
		return rewardFromRank(hc, re, ql);
	}

	private static double valOrZero(double[] arr, int i) {
		return (arr != null && i >= 0 && i < arr.length && Double.isFinite(arr[i])) ? arr[i] : 0.0;
	}
}
