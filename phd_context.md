# PhD Context

Last update: 31/08/2026

---

# 1. Research overview

**Institution**: Institut de Física d'Altes Energies (IFAE)
Supervisor(s): Pol Forn Díaz

**Research topic**: Design and characterization photon counter based on 3D fluxonium qubit.

**Thesis project**: [qrades] — full detail in Section 3. I also collaborate on other projects of the group ([ntd_qubit], [rfsoc]); each one is listed in Section3 with my role in it.

**Current stage:**
- Reading about the state of the art in photon counter based on superconducting qubits.
- Learning how to use COMSOL and Palace to simulate 3D cavities and qubits.


---

# 2. Long-term objective

- Design and make an aluminuin cavity to test design and fabrications skills.
- Design and make a transmon qubit using aliminum to test design and fabrications skills.
- Measure the transmon qubit with the cavity to test the setup and learn how to control and readout a qubit.
- Figure out constraints and requirements to use superconducting layers in 3D cavities.
- Design cavity using copper covered by a superconducting layer to store photons.
- Test the cavity with the qubit to detect photons.
- Design and make a fluxonium qubit using nitrAl to detect photons.
- Try nitrAl fluxonium qubit with the cavity to detect photons.

---

# 3. Projects

Each project states my **Role**: *Lead* (I drive it), *Collaborator* (I own a well-defined part), *Support* (someone else leads, I help).

## QRADES — photon counter with 3D fluxonium `@qrades`

**Role**: Lead — this is my PhD thesis project.
**Time share**: TODO
**Status**: Design phase.

**Overview**: The goal of the project is use superconducting qubits as a sensor for high energy particles, specially for detect dark matter. The interaction betwwen dark matter and a cavity emitted a photon that can be detected by a superconducting qubit.

For that reason, the project proposes use a fluxonium made by nitrAl, a cavity for readout and another biger cavity to ``storage'' the photons. This device will be placed in a high magnetic field to increase the interaction between the dark matter and the cavity (it says the theoriticians, but for us, we want to count photons). The cavity of storage needs to support a high magnetic field, so it won't be made in Aluminium. Instead, it will be made of copper covered by a superconducting layer developed by the material group of UAB

The project is currently in the design phase, where we are simulating and designing fluxonium qubits using nitrated aluminum as a high-kinetic inductance material. We chose this material instead of granular aluminum because reduce the oxide layer that is supposed to generate losses in the qubit.

The group is being developed nitrAl as a new material for superconducting qubits, and it is currently in the process of characterizing the material and its properties. In the other hand, <!-- TODO: sentence unfinished -->

**Goals**
- Make a Aliminuim cavity (both for readout and storage).
- Make a aluminuim fluxonium qubit. (normal fluxonium, not the one with nitrAl)
- Try the setup to control and measure the qubit with cavity.
- Design a copper cavity with superconducting layer to store photons. (talk with collaborators).
- Test the cavity.
- Test cavity with the qubit.
- Test in high magnetic field.

**Ready**
- 

**ToDo**
- 

## NTD + Qubit @ntd_qubit

**Role**: Collaborator: synchronization + DAQ?
**Time share**: 
**Status**: Driver testing.

**Overview**: We want to detect astro particles using qubit syncronized with a NTD. The NTD is a semiconductor that is sensitive to high energy particles. For that reason, is currently used to measure astroparticles. The signal of NTD is collected in a DAQ and sent to a computer. The signal has te follow shape:

- high ramp up to a maximum value: $\sim 100$ us
- exponential decay to the baseline: $\sim 1 - 10$ ms

The idea is to use a transmon qubit to detect the photons.  Since transmon isn't sensible to charge between ground and excited state, we use the transition from the first excited state to the second excited state, which is sensible to charge and measure the variation of the qubit frequency. The $T_2$ of the qubit is $\sim 1 -10 $ us so we are able to perform many measurements while NTD is reacting to the event.

The main goal is synchronize the both signals. We want to perform the experiments in parallel, so we can sent a pulse to control the start point, but we have to be able to have a timestamp of the event in the NTD and the qubit so we can correlate the both signals.

We use a 3D transmon, in which the cavity for the qubit is separated from the cavity where NTD is placed. This cavity was developed by Elsa in her Master Thesis. The cavity is made of copper.

**Equipment**

- NTD: made by germanium
- Qubit: made by aluminum
- Cavity: made by copper
- Qauntum Machine + Octave: to control the qubit and readout the signal
- DAQ NI USB-6289: 32 inputs, 18-bit, 25 kS/s.

**Ready**

- Cavity.
- Qubit.
- DAQ driver #review
- QM drivers and control. #review

**ToDo**
- Figure out how to syncronize the both signals. #research

## RFSoC characterization @rfsoc

**Role**: Support — Biel leads.
**Time share**: TODO
**Status**: DAC↔ADC calibration relation obtained (27/07/2026).

**Overview**: TODO

**Ready**
- Relation between DAC units and dBm (interp2d over gain and frequency), connected to ADC units.

**ToDo**
- Measure full output from frequency analyzer to get a detailed relation between DAC units and dBm, also the bandwidth of the signal that we can inject.
- Improve the documentation of the board.
- Bug fix while switch on the board, it doesn't work well. #bug

---

# 4. Tag reference

Markers used in `research_log.md`. Three markers, three different questions.

| Marker | Question it answers | Required |
|---|---|---|
| `[thread]` | what stream of work is this? | yes |
| `@project` | which project does it feed? | only when specific *and* not obvious |
| `#type` | what kind of entry is it? | free |

A thread with **no `@`** means shared infrastructure or learning that serves every project — the absence is information, not a gap. Add `@` only when the work really was for one project and the thread name doesn't already say so.

```
- [palace] Tested palace with resonator using bw = 1GHz — works fine.        <- infrastructure, no @
- [palace] @qrades Simulated the fluxonium readout cavity, f0 = 7.2 GHz.     <- project-specific
- [rfsoc] Biel is analyzing the data from the last experiment.               <- thread = project, @ redundant
```

## Threads `[...]`

| Thread | Meaning |
|---|---|
| `[palace]` | Palace simulations |
| `[comsol_sim]` | COMSOL simulations |
| `[SQDMetal]` | SQDMetal library / my additions to it |
| `[qm_learning]` | Quantum Machines learning & drivers |
| `[rfsoc]` | RFSoC characterization (Biel leads) |
| `[ntd_qubit]` | NTD + qubit synchronization |
| `[doc]` | Paperwork, admin, reporting |

A thread can be named after a project when the work is project-specific; that is why `[rfsoc]` and `[ntd_qubit]` appear here as well as in §3.

## Projects `@...`

`@qrades` · `@ntd_qubit` · `@rfsoc` — see §3.

## Types `#...`

`#personal` · `#meeting` · `#onboarding` · `#research` · `#feature` · `#fix` · `#bug` · `#review`

---

# 5. Literature map

## Fundamental papers

**Axions:**

- Ankur Agrawal et al. PRL **132**, 140801 (2024).
- Akash V. Dixit et al. PRL **126**, 141302 (2021). `read`
- Fang Zhao et al. PRL **135**, 201002 (2025).
- A. Thery et al. arXiv:2401.04227v1 (2024).

**nitrAl:**

- Alba Torras-Coloma et al. Supercond. Sci. Technol. **37** (2024) 035017 
- Tesis Ariadna.

**Simulation:**
- David Sommers et al. arXiv:2511.01220v2 (2025).
- Axel M. Eriksson et al. arXiv:2508.18027v1 (2025).

## Recently read

- David Sommers et al. arXiv:2511.01220v2 (2025). --- I tested code using SQDMetal with my own design obtaining the same result I obtained using Qiskit Metal and Ansys.

- Akash V. Dixit et al. PRL **126**, 141302 (2021). --- I simulated the HMM with the data they provide in the paper, and I obtained the same result they obtained. I also simulated Ramsey sequence to understand how it works and how to use it in my own data. 



## Papers to read

- the rest..

---

# 6. Current tasks

**High priority**

- Design a simply cavity to simulate in COMSOL.
- Try simulate it in Palace. Palace accept mesh files and a .json configuration file. I need to learn how to use it.
- Compare results between COMSOL and Palace in 3D cavities.

**Medium priority**
- Compare simulations between COMSOL and Palace in 2D resonators.
- Make a report of the simulations and results obtained.
- Look for a easy way to design cavities and then export to COMSOL/Palace.

**Low priority**

- Test user friendly functions in SQDMetal that I made to run simulations in Palace/Comsol.
- PR about save/load function in Quantum Metal.

---

# 7. Open problems

`[ntd_qubit]` How to syncronize the both signals. #research

`@qrades` `[HMM]` from: Akash V. Dixit et al. PRL **126**, 141302 (2021). I would like to test differents approaches to improve the HMM analysis:

- Fit a 2D Gaussian (or make a cumulant expansion) to the IQ data instead of assigning a threshold to each shot and using a constant probability of being ground and read ground ($\mathcal{F}_{g\mathcal{G}}$) or excited and read excited ($\mathcal{F}_{e\mathcal{E}}$). It is **not** the same: thresholding collapses each shot to one bit, so two shots labelled $\mathcal{E}$ carry identical evidence even when their true likelihood ratios differ by five orders of magnitude. Simulated with Dixit's parameters, keeping the raw IQ gives ~1.6x more log-likelihood per shot and raises detection efficiency from 76.4 % to 83.2 % at $\lambda_{thresh} = 10^5$, with no change in the false-positive rate. So we can extract the same information in fewer shots, which is what matters for qubits with low $T_1$ — or equivalently improve the likelihood ratio for the same number of shots. See section 12 for the caveats and what it demands of our calibration.

- Add leakage of the qubit. All the analysis is made in $\{|0g\rangle, |0e\rangle, |1g\rangle, |1e\rangle\}$ space, but the qubit can leak to $|f\rangle$ state, also the photons can leak to the environment. I think photon leakage is less important because the storage cavity is almost empty. Base on $\langle n_{HP} \rangle < 10^{-1}$ (hidden phtons) and $\langle n_{axions} \rangle \sim 10^{-8} - 10^{-5}$ per mesurement,it's not a big problem. 

- 


---

# 8. Current software

**Languages**
- Python
- C++

**Libraries**
- NumPy
- SciPy
- QuTiP
- Qiskit/Quantum Metal
- SQDMetal (frok of Qiskit Metal)
- Own toolbok kit.

**Commercial software**
- HFSS
- Q3D
- Comsol
- Palace (Open source)
---


# 9. Decisions already made

- [qrades] **nitrAl instead of granular aluminum** for the high-kinetic-inductance material — it reduces the oxide layer that is supposed to generate losses in the qubit.
- [qrades] **Storage cavity in copper covered by a superconducting layer** (developed by the material group of UAB) instead of aluminium — the storage cavity has to support a high magnetic field.
- [qrades] **Separate cavities**: one cavity for readout, a bigger one to storage the photons.
- [ntd_qubit] **Use the 1→2 transition** of the transmon instead of 0→1 — the transmon is not sensitive to charge between ground and excited state, so the qubit-frequency shift is read on the charge-sensitive transition.
- [palace] **Order 1 instead of order 2** in drivenmodal S21 — order 2 does not detect sharp resonances (30/07/2026).
- [palace] **Brute-force frequency sweep instead of `AdaptativeTol`** when the resonance is very sharp (28/07/2026).
- [rfsoc] **interp2d** to map DAC units → dBm as a function of gain and frequency (27/07/2026).

---

# 10. Results obtained

- [HMM] Soft simulation of the HMM using the IQ data provided in the paper "Searching for Dark Matter with Superconducting Qubit", Akash V. Dixit et al. 2021. I obtained better discrimination of detection of photons (27/08/2026).
- [SQDMetal] Reproduced with SQDMetal + Palace the same result previously obtained with Qiskit Metal + Ansys, on my own design.
- [palace] S21 of a resonator resolved with 2001 points over bw = 10 MHz centered on the resonance, order 1 (30/07/2026).
- [rfsoc] Relation between DAC units and ADC units established, via DAC units ↔ dBm (27/07/2026).

---

# 11. Next milestone

- Learn how to use COMSOL to simulate 3D cavities and qubits. --- 04/09/2026

---

# 12. Future ideas

## Soft-information readout for the photon-counter HMM @qrades

**Idea.** The standard HMM analysis (Dixit, PRL 126, 141302) thresholds every single
shot into one bit (G/E) *before* the HMM sees it, and the emission matrix E then applies
a constant fidelity (95.8 % / 95.3 %) to that bit. This discards how far the shot sat
from the decision boundary: two shots both labelled E, one deep in the |e> cloud and one
grazing the threshold, carry evidence differing by **five orders of magnitude** but are
treated identically. Feeding the calibrated IQ densities p_g(I,Q), p_e(I,Q) straight into
the emission step recovers that. The HMM itself is unchanged — only the emission
likelihood changes.

Note the loss is entirely in the *thresholding*, not in the 1D projection: with equal
covariances, projecting onto the centre-to-centre line is exactly lossless.

**Simulated gain** (30 parity measurements, Dixit parameters): ~1.6x in log-likelihood
per shot, detection efficiency 76.4 % -> 83.2 % at lambda_thresh = 1e5, with no change in
the false-positive rate. **Treat 1.6x as an upper bound**: that simulation models all
readout infidelity as cloud overlap, but FIG. S6 of Dixit's supplement shows the clouds
separated by ~4.7 sigma (implying ~99 % overlap-limited fidelity), so most of their
reported 4.2 % infidelity is actually T1 decay during the 3 us readout window plus |f>
leakage — which soft information does not fix on its own.

**Before spending effort, check we are readout-limited at all.** Re-run the HMM with
Fgg = Fee = 1.0 (perfect readout). If the false-positive rate barely moves, we are
background- or cavity-limited and this buys margin, not sensitivity. Dixit are explicitly
background-limited ("cavity background photons are the limiting process").

**What this demands of our own calibration:**
- Store **single shots, never averaged**. Averaging gives mu but destroys sigma and the
  tails, which are the whole point. Many repetitions != averaging.
- Need >> 1e4 shots: the tails set the false-positive rate, and that is where the
  statistics have to be good.
- Fit a **covariance matrix per state** (QDA), not one isotropic sigma — the clouds are
  anisotropic, and the optimal projection direction is Sigma^-1 (mu_e - mu_g), not
  (mu_e - mu_g). Worth 1.2-2.8x in SNR^2 depending on how elongated they are.
- Add explicit mixture components for **decay during readout** (weight t_m/T1_q, roughly
  uniform bridge between the clouds) and for **|f> leakage**. Gaussian tails underestimate
  the true density by 30-130x at 2.5-3 sigma from the centre.
- Validate on a **log-scale histogram**: a Gaussian is an exact parabola there, so any
  bridge or third blob is immediately visible.
- Feed the HMM the **likelihood** p(y|s), never a classifier posterior P(s|y) — the HMM
  supplies its own prior through T @ p, so a posterior double-counts it.

**Deeper fix worth considering for our device:** time-resolved readout — keep the readout
window as several time bins instead of collapsing it to one complex number. A mid-readout
T1 decay then appears as a visible step rather than an ambiguous smeared point, which
attacks the dominant error at its root instead of modelling around it. It also repairs a
structural flaw: T and E are assumed independent, but decay-during-readout correlates them.

Working simulation: `src/sim/redout/HMM_redout.ipynb` + `readout_IQ.ipynb`.
