import logging

import numpy as np
import pytest

import nengo
import nengo.simulator
from nengo.builder import Model
from nengo.builder.node import build_pyfunc
from nengo.builder.operator import Copy, Reset, DotInc, SimNoise
from nengo.builder.signal import Signal
from nengo.solvers import LstsqL2nz
from nengo.utils.compat import range
from nengo.utils.functions import whitenoise


logger = logging.getLogger(__name__)


def test_steps(RefSimulator):
    m = nengo.Network(label="test_steps")
    sim = RefSimulator(m)
    assert sim.n_steps == 0
    sim.step()
    assert sim.n_steps == 1
    sim.step()
    assert sim.n_steps == 2


def test_time_steps(RefSimulator):
    m = nengo.Network(label="test_time_steps")
    sim = RefSimulator(m)
    assert np.allclose(sim.signals["__time__"], 0.00)
    sim.step()
    assert np.allclose(sim.signals["__time__"], 0.001)
    sim.step()
    assert np.allclose(sim.signals["__time__"], 0.002)


def test_time_absolute(Simulator):
    m = nengo.Network()
    sim = Simulator(m)
    sim.run(0.003)
    assert np.allclose(sim.trange(), [0.001, 0.002, 0.003])


def test_trange_with_probes(Simulator):
    dt = 1e-3
    m = nengo.Network()
    periods = dt * np.arange(1, 21)
    with m:
        u = nengo.Node(output=np.sin)
        probes = [nengo.Probe(u, sample_every=p, synapse=5*p) for p in periods]

    sim = Simulator(m, dt=dt)
    sim.run(0.333)
    for i, p in enumerate(periods):
        assert len(sim.trange(p)) == len(sim.data[probes[i]])


def test_signal_indexing_1(RefSimulator):
    one = Signal(np.zeros(1), name="a")
    two = Signal(np.zeros(2), name="b")
    three = Signal(np.zeros(3), name="c")
    tmp = Signal(np.zeros(3), name="tmp")

    m = Model(dt=0.001)
    m.operators += [
        Reset(one), Reset(two), Reset(tmp),
        DotInc(Signal(1, name="A1"), three[:1], one),
        DotInc(Signal(2.0, name="A2"), three[1:], two),
        DotInc(
            Signal([[0, 0, 1], [0, 1, 0], [1, 0, 0]], name="A3"), three, tmp),
        Copy(src=tmp, dst=three, as_update=True),
    ]

    sim = RefSimulator(None, model=m)
    sim.signals[three] = np.asarray([1, 2, 3])
    sim.step()
    assert np.all(sim.signals[one] == 1)
    assert np.all(sim.signals[two] == [4, 6])
    assert np.all(sim.signals[three] == [3, 2, 1])
    sim.step()
    assert np.all(sim.signals[one] == 3)
    assert np.all(sim.signals[two] == [4, 2])
    assert np.all(sim.signals[three] == [1, 2, 3])


def test_simple_pyfunc(RefSimulator):
    dt = 0.001
    time = Signal(np.zeros(1), name="time")
    sig = Signal(np.zeros(1), name="sig")
    m = Model(dt=dt)
    sig_in, sig_out = build_pyfunc(m, lambda t, x: np.sin(x), True, 1, 1, None)
    m.operators += [
        Reset(sig),
        DotInc(Signal([[1.0]]), time, sig_in),
        DotInc(Signal([[1.0]]), sig_out, sig),
        DotInc(Signal(dt), Signal(1), time, as_update=True),
    ]

    sim = RefSimulator(None, model=m)
    for i in range(5):
        sim.step()
        t = i * dt
        assert np.allclose(sim.signals[sig], np.sin(t))
        assert np.allclose(sim.signals[time], t + dt)


def test_probedict():
    """Tests simulator.ProbeDict's implementation."""
    raw = {"scalar": 5,
           "list": [2, 4, 6]}
    probedict = nengo.simulator.ProbeDict(raw)
    assert np.all(probedict["scalar"] == np.asarray(raw["scalar"]))
    assert np.all(probedict.get("list") == np.asarray(raw.get("list")))


def test_reset(Simulator, nl_nodirect, seed):
    """Make sure resetting actually resets.

    A learning network on weights is used as the example network as the
    ultimate stress test; lots of weird stuff happens during learning, but
    if we're able to reset back to initial connection weights and everything
    then we're probably doing resetting right.
    """
    noise = whitenoise(0.1, 5, dimensions=2, seed=seed + 1)
    m = nengo.Network(seed=seed)
    with m:
        m.config[nengo.Ensemble].neuron_type = nl_nodirect()
        u = nengo.Node(output=noise)
        ens = nengo.Ensemble(200, dimensions=2)
        error = nengo.Ensemble(200, dimensions=2)
        square = nengo.Ensemble(200, dimensions=2)

        nengo.Connection(u, ens)
        nengo.Connection(u, error)
        nengo.Connection(square, error, transform=-1)
        err_conn = nengo.Connection(error, square, modulatory=True)
        nengo.Connection(ens, square,
                         learning_rule_type=[nengo.PES(err_conn), nengo.BCM()],
                         solver=LstsqL2nz(weights=True))

        square_p = nengo.Probe(square, synapse=0.1)
        err_p = nengo.Probe(error, synapse=0.1)

    sim = Simulator(m)
    sim.run(0.2)
    sim.run(0.3)

    first_t = sim.trange()
    first_square_p = np.array(sim.data[square_p], copy=True)
    first_err_p = np.array(sim.data[err_p], copy=True)

    sim.reset()
    sim.run(0.5)

    assert np.all(sim.trange() == first_t)
    assert np.all(sim.data[square_p] == first_square_p)
    assert np.all(sim.data[err_p] == first_err_p)


def test_noise(RefSimulator, seed):
    """Make sure that we can generate noise properly."""

    n = 1000
    mean, std = 0.1, 0.8
    noise = Signal(np.zeros(n), name="noise")
    dist = nengo.dists.Gaussian(mean, std)

    m = Model(dt=0.001)
    m.operators += [Reset(noise), SimNoise(noise, dist)]

    sim = RefSimulator(None, model=m, seed=seed)
    samples = np.zeros((100, n))
    for i in range(100):
        sim.step()
        samples[i] = sim.signals[noise]

    h, xedges = np.histogram(samples.flat, bins=51)
    x = 0.5 * (xedges[:-1] + xedges[1:])
    dx = np.diff(xedges)
    z = 1./np.sqrt(2 * np.pi * std**2) * np.exp(-0.5 * (x - mean)**2 / std**2)
    y = h / float(h.sum()) / dx
    assert np.allclose(y, z, atol=0.02)


if __name__ == "__main__":
    nengo.log(debug=True)
    pytest.main([__file__, "-v"])
