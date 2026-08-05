# PhD Context

Last update: 05/08/2026

---

# 1. Research overview

**Institution**: Institut de Física d'Altes Energies (IFAE)
Supervisor(s): Pol Forn Díaz

**Research topic**: Design and characterization photon counter based on 3D fluxonium qubit.

The goal of the project is use superconducting qubits as a sensor for high energy particles, specially for detect black matter. The interaction betwwen black matter and a cavity emitted a photon that can be detected by a superconducting qubit. 

For that reason, the project proposes use a fluxonium made by nitrAl, a cavity for readout and another biger cavity to ``storage'' the photons. This device will be placed in a high magnetic field to increase the interaction between the black matter and the cavity (it says the theoriticians, but for us, we want to count photons). The cavity of storage needs to support a high magnetic field, so it won't be made in Aluminium. Instead, it will be made of copper covered by a superconducting layer developed by the material group of UAB 

The project is currently in the design phase, where we are simulating and designing fluxonium qubits using nitrated aluminum as a high-kinetic inductance material. We chose this material instead of granular aluminum because reduce the oxide layer that is supposed to generate losses in the qubit.

The group is being developed nitrAl as a new material for superconducting qubits, and it is currently in the process of characterizing the material and its properties. In the other hand, 

**Current stage:**
- Onboarding and learning about the project. Installing tools.


**Goals**
- Make a Aliminuim cavity (both for readout and storage).
- Make a aluminuim fluxonium qubit. (normal fluxonium, not the one with nitrAl)
- Try the setup to control and measure the qubit with cavity.
- Design a copper cavity with superconducting layer to store photons. (talk with collaborators).
- Test the cavity.
- Test cavity with the qubit.
- Test in high magnetic field.

---

# 2. Long-term objective



---

# 3. Projects

## NTD + Qubit [ntd_qubit]

**Overview**: We want to detect astro particles using qubit syncronized with a NTD. The NTD is a semiconductor that is sensitive to high energy particles. For that reason, is currently used to measure astroparticles. The signal of NTD is collected in a DAQ and sent to a computer. The signal has te follow shape:

- high ramp up to a maximum value: $\sim 100$ us
- exponential decay to the baseline: $\sim 1 - 10$ ms

The idea is to use a transmon qubit to detect the photons.  Since transom isn't sensible to charge between ground and excited state, we use the transition from the first excited state to the second excited state, which is sensible to charge and measure the variation of the qubit frequency. The $T_2$ of the qubit is $\sim 1 -10 $ us so we are able to perform many measurements while NTD is reacting to the event.

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

## QRADES
Is the project which is involved my PhD.


# 6. Literature map

## Fundamental papers

**Axions:**

- Ankur Agrawal et al. PRL **132**, 140801 (2024).
- Akash V. Dixit et al. PRL **126**, 141302 (2021).
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

## Papers to read

- the rest..

---

# 7. Current tasks

**High priority**

- Try DAQ driver.
- Try QM driver and control.
- Figure out how to syncronize the both signals. #research

**Medium priority**
- Compare simulations between COMSOL and Palace.
- Make a report of the simulations and results obtained.
- Read fundamental papers.
- Learn using COMSOL in 3D cavities.

**Low priority**

- Debug class I made in SQDMetal.
- Make user friendly functions in SQDMetal to run simulations using Comsol.

---

# 8. Open problems

[ntd_qubit] How to syncronize the both signals. #research


---

# 9. Current software

Languages

- Python
- C++

Libraries

- NumPy
- SciPy
- QuTiP
- Qiskit

Commercial software

- HFSS
- Q3D
- Comsol
- Palace (Open source)
---


# 11. Decisions already made


---

# 12. Results obtained


---

# 13. Next milestone


---

# 14. Future ideas
