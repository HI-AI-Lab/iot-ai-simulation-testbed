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
 * Double-DQN agent with uniform replay.
 * Supports dynamic input size based on feature mask.
 */
public class Agent implements Serializable {

    // ------------ Public data holders for controller inputs ------------

    public static class Counters {
        public int generated;
        public int delivered;
        public int dropped;
        public double residualEnergy;
        public int etx;
        public int hopCount;
        public int rankViolations;
    }

    // ------------ Internal episode & replay structures ------------

    private static class Episode {
        final double[] sFlat;
        final int a;
        final Counters c0;
        Episode(double[] sFlat, int a, Counters c0) {
            this.sFlat = sFlat; this.a = a; this.c0 = c0;
        }
    }

    private static class Transition {
        final double[] s; final int a; final double r;
        final double[] s2; final boolean done; final boolean[] valid2;
        Transition(double[] s, int a, double r, double[] s2, boolean done, boolean[] valid2) {
            this.s = s; this.a = a; this.r = r; this.s2 = s2; this.done = done; this.valid2 = valid2;
        }
    }

    // ------------ Hyperparameters / knobs ------------

    private final int k;                 // candidate parents
    private final int Ftotal;            // total features available
    private final boolean[] featureMask; // true = active feature
    private final int Factive;           // active features per parent

    private double epsilon = 0.10;
    private double gamma   = 0.90;
    private int    batchSize = 32;
    private int    targetUpdateEvery = 5;

    private double alphaETX = 0.01;
    private double betaQLR  = 0.50;
    private double gammaRV  = 0.20;

    // ------------ State ------------

    private final Map<Integer, Episode> open = new HashMap<>();
    private static final int REPLAY_CAPACITY = 100_000;
    private final Deque<Transition> replay = new ArrayDeque<>(REPLAY_CAPACITY);

    private final Random rng = new Random(1234);
    private final MultiLayerNetwork online;
    private final MultiLayerNetwork target;
    private long trainSteps = 0;

    // ------------ Construction ------------

    public Agent(int k, boolean[] mask) {
        this.k = k;
        this.Ftotal = mask.length;
        this.featureMask = Arrays.copyOf(mask, mask.length);

        int cnt = 0;
        for (boolean b : mask) if (b) cnt++;
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

    // ------------ Controller-facing API ------------

    public void ping() {}

    public int decide(int moteId, double[][] S, boolean[] valid, Counters countersNow) {
        double[] flat = flattenState(S);

        Episode prev = open.remove(moteId);
        if (prev != null) {
            double r = computeReward(prev.c0, countersNow);
            Transition t = new Transition(prev.sFlat, prev.a, r, flat, false, copyOrAllValid(valid));
            addReplay(t);
        }

        int a = selectAction(flat, valid);
        open.put(moteId, new Episode(flat, a, cloneCounters(countersNow)));
        return a;
    }

    public void endPhase(Map<Integer, double[][]> S2ByMote,
                         Map<Integer, boolean[]> valid2ByMote,
                         Map<Integer, Counters> countersByMote) {

        for (Map.Entry<Integer, Episode> e : open.entrySet()) {
            int moteId = e.getKey();
            Episode ep = e.getValue();

            double[][] S2 = S2ByMote != null ? S2ByMote.get(moteId) : null;
            boolean[] valid2 = valid2ByMote != null ? valid2ByMote.get(moteId) : null;
            Counters cNow = countersByMote != null ? countersByMote.get(moteId) : null;
            if (cNow == null) continue;

            double[] s2Flat = (S2 != null) ? flattenState(S2) : ep.sFlat;
            boolean[] v2 = copyOrAllValid(valid2);

            double r = computeReward(ep.c0, cNow);
            addReplay(new Transition(ep.sFlat, ep.a, r, s2Flat, true, v2));
        }
        open.clear();

        int D = replay.size();
        int nBatches = (int) Math.ceil(D / (double) Math.max(1, batchSize));
        trainStep(nBatches);
    }

    // ------------ Training internals (Double-DQN) ------------

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
                X.putRow(i, Nd4j.createFromArray(t.s));

                double[] qCurr = forward(online, t.s);
                double targetQ;
                if (t.done) {
                    targetQ = t.r;
                } else {
                    int aStar = argmaxMasked(forward(online, t.s2), t.valid2);
                    targetQ = (aStar < 0) ? t.r : t.r + gamma * forward(target, t.s2)[aStar];
                }
                qCurr[t.a] = targetQ;
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

    // ------------ Q-network helpers ------------

    private double[] forward(MultiLayerNetwork net, double[] flatState) {
        INDArray out = net.output(Nd4j.createFromArray(flatState).reshape(1, k * Factive), false);
        return out.toDoubleVector();
    }

    private int selectAction(double[] flat, boolean[] valid) {
        double[] q = forward(online, flat);
        int greedy = argmaxMasked(q, valid);
        if (greedy < 0) return 0;
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
            for (int i = 0; i < q.length; i++)
                if (q[i] > bestVal) { bestVal = q[i]; best = i; }
            return best;
        }
        for (int i = 0; i < Math.min(q.length, valid.length); i++) {
            if (valid[i] && q[i] > bestVal) { bestVal = q[i]; best = i; }
        }
        return best;
    }

    private void hardSyncTarget() {
        target.setParams(online.params().dup());
    }

    // ------------ Replay & reward helpers ------------

    private void addReplay(Transition t) {
        if (replay.size() == REPLAY_CAPACITY) replay.removeFirst();
        replay.addLast(t);
    }

    private double computeReward(Counters c0, Counters c1) {
        int gen  = c1.generated - c0.generated;
        int del  = c1.delivered - c0.delivered;
        int drop = c1.dropped   - c0.dropped;
        if (gen < 0 || del < 0 || drop < 0) {
            gen = Math.max(0, gen); del = Math.max(0, del); drop = Math.max(0, drop);
        }
        double pdr = gen > 0 ? (double) del / gen : 0.0;
        double qlr = gen > 0 ? (double) drop / gen : 0.0;
        double etx = c1.etx;
        int rv  = Math.max(0, c1.rankViolations - c0.rankViolations);
        return pdr - alphaETX * etx - betaQLR * qlr - gammaRV * rv;
    }

    private double[] flattenState(double[][] S) {
        double[] out = new double[k * Factive];
        int idx = 0;
        for (int i = 0; i < k; i++) {
            double[] row = (S != null && i < S.length) ? S[i] : null;
            for (int j = 0; j < Ftotal; j++) {
                if (featureMask[j]) {
                    out[idx++] = (row != null && j < row.length) ? row[j] : 0.0;
                }
            }
        }
        return out;
    }

    private static boolean[] copyOrAllValid(boolean[] valid) {
        if (valid == null) return null;
        return Arrays.copyOf(valid, valid.length);
    }

    private static Counters cloneCounters(Counters c) {
        Counters d = new Counters();
        d.generated = c.generated;
        d.delivered = c.delivered;
        d.dropped = c.dropped;
        d.residualEnergy = c.residualEnergy;
        d.etx = c.etx;
        d.hopCount = c.hopCount;
        d.rankViolations = c.rankViolations;
        return d;
    }

    // ------------ Introspection / tuning ------------

    public int getReplaySize() { return replay.size(); }
    public int getBatchSize()  { return batchSize; }
    public void setBatchSize(int b) { batchSize = Math.max(1, b); }
    public void setGamma(double g)   { gamma = g; }
    public void setEpsilon(double e) { epsilon = Math.max(0.0, Math.min(1.0, e)); }
    public void setTargetUpdateEvery(int c) { targetUpdateEvery = Math.max(1, c); }
    public void setRewardWeights(double alphaETX, double betaQLR, double gammaRV) {
        this.alphaETX = alphaETX; this.betaQLR = betaQLR; this.gammaRV = gammaRV;
    }
}
