# Research Log

## 28/08/2026 - friday

**Done**
- [biblio] Literature search on nitride-based transmons (NbN/AlN/NbN). Main line is NICT/Terai: 2011 on MgO (T1 ~0.5 us) -> 2021 on Si + TiN buffer (T1 = 16.3 us). The junction never changed, only the substrate. #learn
- [biblio] LinkedIn post from a conference (Michaela Eichinger) with NICT slides on the "Epimon" (all-nitride epitaxial transmon), unpublished:
  - Chip1Q4: T1 = 1.016 ms, f01 = 2.938 GHz, Q = 1.875e7.
  - Chip1Q2: T1 distribution over 40 h, mean 523 us with sigma = 4% of the mean. The Al reference on the same slide spreads ~20%.
  - The interesting number is the 4% stability, not the millisecond.

**ToDo**
- [biblio] Watch arXiv for the Epimon preprint (NICT: Terai, Kim). Keyword "Epimon".
- [biblio] Check the citation on the slide, "Appl. Phys. Express 10, 060102 (2026)" - volume 10 would be 2017, so the volume or the year is wrong.

## 27/08/2026 - thursday
**Done**
- [HMM] I performed `soft simulation`, it means using normal distribution to calculate probability of the readout. How I don't have the full IQ data, I did the following:

  - Assume that the threshold is located in the middle of the two centers of the Gaussian distribution, which are reported on supplementary material. 
  - Assume the probabilities $\mathcal{F}_{g\mathcal{G}}$ and $\mathcal{F}_{e\mathcal{E}}$ are calculated using $\Phi(\frac{threshold - \mu_g}{\sigma_g})$ and $\Phi(\frac{threshold - \mu_e}{\sigma_e})$, where $\Phi$ is the cumulative distribution function of the normal distribution, $\mu_g$ and $\mu_e$ are the centers of the Gaussian distribution for ground and excited state, respectively, and $\sigma_g$ and $\sigma_e$ are the standard deviations of the Gaussian distribution for ground and excited state, respectively. $\mu_g$, $\mu_e$ are reported on supplementary material and I calculated the threshold as the middle point between $\mu_g$ and $\mu_e$. So, I can calculate $\sigma_g$ and $\sigma_e$ using the reported values of $\mathcal{F}_{g\mathcal{G}}$ and $\mathcal{F}_{e\mathcal{E}}$ in the paper.
  - I define the Emission Matrix using normal distribution density function. In script `HMM_readout.ipynb` I define D(R) as: $$D(R) = \text{diag} \left[p_g(R), p_e(R), p_g(R), p_e(R)\right]$$ where $p_g(R)$ and $p_e(R)$ are the probability density function of the normal distribution for ground and excited state.
  - Use the same algorithm by using the Emission Matrix defined above instead of using the threshold to assign the measurement to ground or excited state.

- [HMM] I compare the results of the `soft simulation` with the results of the paper "Searching for Dark Matter with Superconducting Qubit", Akash V. Dixit et al. 2021. I obtained better discrimination of detection of photons.

## 26/08/2026 - wednesday
**Done**
- [HMM] I simulate the backward algorithm to estimate the number of photons in the cavity. I compare my results with the results of the paper "Searching for Dark Matter with Superconducting Qubit", Akash V. Dixit et al. 2021. I obtain the same result as the paper, so I can use my code to estimate the number of photons in the cavity using the backward algorithm. #learn

**TODO**:
- [HMM] They use a threshold to assign the measurement to ground or excited state, and then they use a constant probability of being ground and read ground ($\mathcal{F}_{g\mathcal{G}}$) or excited and read excited ($\mathcal{F}_{e\mathcal{E}}$). I think that measures near to the center point of the Gaussian distribution are more likely to be correct than measures near to the threshold. Ideally, I would like to fit a 2D Gaussian (or make a cumulant expansion) to the IQ data instead of assigning a threshold to each shot and use IQ data measurement as an input to the algorithm. However, I don't have the IQ data. 

## 25/08/2026 - tuesday
**Done**
- [HMM] I understand that we use a backward algorithm to estimate the number of photons in the cavity. It means, $X_i$ is the hidden state of the qubit and $\mathbf{R} = \{R_1, R_2, ..., R_n\}$ is the sequence of measurements. What I need to calculate is:

$$
P(\mathbf{R} | X_i) = P(R_1, R_2, ..., R_n | X_i)
$$

it means, the probability of the sequence of measurements given the hidden state of the qubit, not the probability of the hidden state given the sequence of measurements.

Yesterday I thought I need to calculate:

$$
P(X_i | \mathbf{R}) = P(X_i | R_1, R_2, ..., R_n)
$$

but forward algotithm calculates $X_f = \prod_{i=1}^{N} D(R_k) T D(R_0) X_0$$, so in each step I calculate the evolution of the hidden state, which add uncertainty in the hidden state, and then I calculate the probability of the measurement given the hidden state, which give me knowledge about the hidden state. So, it's a trade off between uncertainty and knowledge, and the final result is the probability of the sequence of measurements given the hidden state of the qubit. On the other hand, the backward algorithm, the state $X_0$ is fixed and I have the measurement vector. So the probability to obtain the sequence of measurements given the hidden state of the qubit is just the product, which reduce exponentially the uncertainty in the hidden state, and give me a better estimation of the number of photons in the cavity.

**ToDo**
- [HMM] Continue with *Detector characterization* section.


## 24/08/2026 - monday
**Done**
- [biblio] I read about Hidden Markov Processes. #learn
- [HMM] I made a notebook to simulate Ramsey interferometry and photon counting in a cavity. I tested forward algorithm and Viterbi algorithm to estimate the number of photons in the cavity. #learn

**Errors**
- [HMM] I didn't replicate the figure 2c of the paper "Searching for Dark Matter with Superconducting Qubit", Akash V. Dixit et al. 2021. I need to understand better how photons are counted.

**ToDo**
- [HMM] I need to understand better how photons are counted in the paper "Searching for Dark Matter with Superconducting Qubit", Akash V. Dixit et al. 2021. #learn

## 21/08/2026 - friday

**Done**
- [HMM] Notebook simulating Ramsey interferometry and photon counting in a cavity. Compare between RAW and numerical solution of Schrodinger equation. #learn
- [biblio] First read of Hidden Markov Processes. #learn

**Errors**
- OneDrive is still not fully synchronize.

## 20/08/2026 - thursday

**Done**
- Understand how Ramsey interferometry works. #learn
- Understand how to use Ramsey interferometry to count photons in a cavity. #learn
- I made maths for both, Ramsey interferometry and photon counting in a cavity. #learn
- Crush course about Quantum Machine uses. #learn

**Errors**
- Synchronization of OneDrive. I can't access to my files in OneDrive, I need to wait until fully download necessary files to my computer. #software

**ToDo**
- Make a notebook to simulate Ramsey interferometry and photon counting in a cavity. #learn
- Make a package from quantum system I made in my thesis to easily simulate quantum systems. #software
- [biblio] Read about punch out in readout of a cavity. Ari shows me a measurement of punch out in readout of a cavity, varying the power of the readout pulse and frequency, and I need to understand how it works. She said that we can detect if qubit is alive or dead using that. Read  David Lopez thesis. #learn
- [SQDMetal] Try to connect HFSS and Q3D icons from quantum Metal to palace or comsol to simulate. #feature

## 19/08/2026 - wednesday

**Done**
- Cite for TIE card. #onboarding

## 18/08/2026 - tuesday

**Done**
- [biblio] Read paper "Searching for Dark Matter with Superconducting Qubit", Akash V. Dixit et al. 2021. 

**Errors** 
- 

**ToDo**
- [biblio] Fully understand paper.

## 17/08/2026 - monday

**Done**
- [daq_driver] I tested sucessfully the DAQ driver using markers and define the first as a trigger.
- 

**Errors** 
- 

**ToDo**
- [daq_driver] Wait until measure cavity with transmon and NTD to test the DAQ driver in real conditions.



## 14/08/2026 - friday

**Done**
- [daq_driver] I added `ao.reset()`, so the thing that accumulates can be cleared. Roles accumulate because `ao.setup()` only touches the channels you name - which is the right default for outputs, and is also why a later `setup({"ao0": ...})` can return two channels and break an unpacking. `reset()` writes 0 V to every driven pin BEFORE switching it off, because 'off' does not mean 0 V: an untimed output holds its last value indefinitely, so switching a 5 V line off leaves 5 V on the screw.

**Errors**
- 

**ToDo**
- [daq_driver] Leave the driver alone and go and read about Quantum Machines.


## 13/08/2026 - thursday

**Done**
- [daq_driver] I added the MARKER counter: a second counter that records the scan index of every pulse on a PFI line. It is the same mechanism as the 1 pps - I pulled it out into `_stamper.py` and both inherit from it - pointed at a different wire. Point one at the rubidium and you get the ruler; point the other at the OPX and you get events, in the same units.
- [daq_driver] I put the trigger and the markers on ONE line. A PFI input fans out inside the board, so one wire can feed the start trigger and a counter at once: the pulse that starts the acquisition is also its first marker, and it lands at scan 0 - which is where the trigger is BY DEFINITION. So `marks[0] == 0` is the answer, not an artefact. One cable fewer.
- [daq_driver] Only two counters exist and it looked like three things wanted one. It is two: a start trigger is not timestamped, it DEFINES scan 0, so the timing engine handles it directly and it consumes no counter.
- [daq_driver] Markers are NOT filtered, unlike the 1 pps. There is no test a marker can fail - events are irregular, so every interval is plausible and any filter would be guessing. Repeats are kept and reported.
- [daq_driver] I added `daq.terminals()`, `daq.terminal_roles()` and the board pinout on the instrument, with the ground pin listed next to each terminal. PFI 8-15 each sit next to a D GND pin.
- [daq_driver] I moved the 1 pps to PFI 9 (screw terminal 83) and drive PFI 8 from ao1 everywhere. I dropped the AO trigger arrangement that cannot work.
- [daq_driver] I fixed a trigger near a second boundary shifting the time axis, and made `long_run` survive a trigger wait and record when the trigger actually fired.
- [daq_driver] I fixed the two artefacts on a triggered waveform. The SPIKE is a re-range: the whole AO task shares one output range, so changing a DC level while generating re-sizes it, the task is rebuilt, and the DAC code it was holding is reinterpreted in the new scale - a +1 V sample on +/-1.2 V becomes +5 V on +/-6 V. That is a real spike on a real output. `ao.v_range` pins the range so it cannot happen.
- [daq_driver] `ao_use_only_on_brd_mem` has to be set on ALL channels at once via `.all`, never one at a time in a loop. DAQmx requires it to hold the same value for every channel on the device (-200106), so the moment the loop has set it on the first of two the task is in a state the driver rejects - and it raises on the NEXT access, which is the loop's own iteration, so the traceback points at the iteration rather than at the assignment. Only bites with two or more AO channels, which is why a single-channel waveform never showed it.
- [daq_driver] I made `ao.setup()`'s return predictable and fixed the notebooks that relied on it.
- [daq_driver] I wrote `usb6289_tutorial.ipynb` - the driver explained, easiest thing first - annotated the working notebook in place keeping its cells and outputs, and added a marker section to both notebooks built around a 10-minute bench trial with three plots that check it.
- [daq_driver] I stopped `long_run` writing an empty final segment, added whole-run readers, and made `load_segment` say which segments DO exist when it cannot find one.

**Errors**
- [daq_driver] A marker sent before the AI task arms triggers nothing and STILL latches a 0 into the marker table - a mark with no acquisition behind it. Whatever emits the pulse has to be launched from `ai.set_on_armed()`.

**ToDo**
- [daq_driver] Clear the roles that accumulate across `ao.setup()` calls.


## 12/08/2026 - wednesday

**Done**
- [daq_driver] I added `livescope`: navigate a run's files while they are still being written.
- [daq_driver] I built `acquire()` on top of `acquire_chunks()`, so there is one streaming path instead of two, and kept the two things that made them separate.
- [daq_driver] I summarised the in-RAM live buffer by min/max per block instead of striding. Striding hides a dropout; min/max keeps the EXTREMES of each block, which is what "did the signal ever drop out?" actually asks.
- [daq_driver] I documented `live_decimate`, dropped the attribute nothing reads, fixed the deque length, and made the live plot actually redraw on the inline backend.
- [daq_driver] `clean_edges()` now runs by DEFAULT on read, and I added `edge_report()` to check the 1 pps table before trusting it.
- [daq_driver] I flipped `start_armed` to True by default and added a `long_run` notebook.
- [daq_driver] The OPX callback arms on the FIRST `long_run` task only - a DaqError restarts the acquisition, which builds a new task, which arms, which would otherwise call it again.

**Errors**
- [daq_driver] A duplicate first edge stopped the cleaning from happening at all - so the filtering silently did not run and the ppm came out enormous. Fixed.

**ToDo**
- [daq_driver] Mark WHEN things happen, not just how many samples elapsed. The 1 pps gives a ruler; it does not say where the interesting moments are.


## 11/08/2026 - tuesday

**Done**
- [daq_driver] I rewrote the USB-6289 driver as QCoDeS submodules with multichannel AI - the ToDo from yesterday. The rule for where a setting lives is "who does the hardware let decide it?": decided per pin -> on the channel (`ai0.v_max`, `ao0.dc`), decided once per task -> on the subsystem (`ai.rate`, `ao.freq`), because there is one ADC and one AO clock.
- [daq_driver] I fixed four defects found reviewing the v2 driver.
- [daq_driver] I retired the AI consumption ledger and added `daq.check()`, which validates the whole configuration and returns the problems WITHOUT touching hardware.
- [daq_driver] `long_run()` now writes one file per channel by default, and I added a runnable example.
- [daq_driver] The 1 pps counter now REJECTS values that are not whole atomic seconds. Every acquisition ends with one latched value that is not a second - when the AI task stops the sample clock stops and the count freezes - and a triggered one adds several at the start for the same reason. Left in they destroy the rate estimate: on a 60 s record a 16 ms straggler turned +14 ppm into +16,403 ppm. The raw table is kept for forensics and `edges.i64` is still written raw, so nothing is thrown away.
- [daq_driver] `long_run()` writes the 1 pps edges still queued when the run ends.

**Errors** 
- [daq_driver] The `long_run()` trouble from yesterday was structural - it is what the submodule rewrite was for.

**ToDo**
- [daq_driver] Be able to look at a long run while it is still being written.


## 10/08/2026 - monday

**Done**
- [daq_driver] I decide to use DAQ internal counter to measure how many samples are acquired in 1PPS from rubidium clock.
- [daq_driver] I tested the counter using the DAQ internal clock, it works fine. I can see the number of samples acquired in 1PPS.
- [daq_driver] I compared the timestamp of the 1PPS from rubidium clock and the timestamp from the DAQ internal clock. In a minute we have an error in order to 140 ppm.

**Errors** 
- [daq_driver] Function `long_run()` doesn't work well. This function is used to acquire data for a long time in a thread and store the data in the disk at the same time. 

**ToDo**
- [daq_driver] Split code in QCoDeS submodules to make it more readable and easy to maintain.


## 07/08/2026 - friday

**Done**
- [daq_driver] I testes v1 of the driver to measure a square wave in loopback using QCoDeS adapted driver. It works fine, I can see the square wave in the output. 

**Errors** 
- .

**ToDo**
- [daq_driver] I will figure out timestamp and syncronization of the measurements using QCoDeS adapted driver.


## 06/08/2026 - thrursday

**Done**
- [daq_driver] Measure square wave in loopback using measure and wave_out functions written in python
**Errors** 
- .

**ToDo**
- [daq_driver] I will adapt driver to QCoDeS package to use it in the lab.


## 05/08/2026 - wednesday

**Done**
- [daq_driver] I installed NI driver to connect with the DAQ card
**Errors** 
- .

**ToDo**
- [daq_driver] I will try to connect with the DAQ card using the NI driver.

## 04/08/2026 - tuesday

**Done**
- I read the documentation of Quantum Machine User https://docs.quantum-machines.co/latest/

**Errors** 
- .

**ToDo**
- I will try to run the example of Quantum Machine.

## 03/08/2026 - monday

**Done**
- LinkedIn post about end of "licenciatura". #personal
- Paid to lawyer Luciano for farm lease. #personal
- Sent email to fly refund. #personal
- I chose a work computer, I will use the one from the lab. #personal
- I filled in factorial the schedule of the last week. #doc
- I sent an email to give another drive license document. #personal

**Errors** 
- .

**ToDo**
- 

## 31/07/2026 - friday

**Done**
- [comsol_sim] Run simulation of S21 in PIC

**Errors** 
- .

**ToDo**
- .

## 30/07/2026 - thursday

**Done**
- Meeting with Pol. #meeting
- [comsol_sim] I tested COMSOL with a simple resonator.
- [palace] I tested palace with a SQDMetal design in resonator to obtain S21. It works fine, I can see the resonance in S21, also with 2001 point in a bw = 10 MHz centerer in the resonance. For that, I used order 1, because order 2 doesn't work well with the resonance.
- [SQDMetal] I added 4 funtion to easily run simulations using Palace. #feature

**Errors** 
- [comsol_sim] We can't be more than one connected to COMSOL at the same time because it uses a license. 

**ToDo**
- [comsol_sim] Test COMSOL with a SQDMetal design in resonator to obtain S21.
- [palace] Compare AdapatativeTol with brute force in S21 simulation.
- [palace] [comsol_sim] Compare COMSOL and palace results in S21 simulation.
- [SQDMetal] Test the new functions to run simulations using Palace.


<!-- ------------------------------------------------------------------ -->
## 29/07/2026 - wednesday

**Done**
- Add save and load functionality to SQDMetal to save and load designs, without the need to run the whole notebook or export to GDS. #fix
- [palace] Tested palace with resonator using bw = 1GHz and 501 points, it works fine. I can see the resonance in S21.
- Group meeting with Biel, we discussed how rfsoc works. #meeting
- I made functions in toolkit/sim to easily run eigenmode, dirvenmodal, capacitance and inductance simulations. #feature

**Errors** 
- [palace] Problems using order 2 un drivenmodal simulation: doesn't detect the resonance. 

**ToDo**
- [palace] Compare between solver's order.a
- [palace] Compare AdaptiveTool.
- [palace] Compare number of points in the frequency sweep.


## 28/07/2026 - tuesday

**Done**
- get medical assist card. #onboarding
- [rfsoc] Biel is making the presentation for Wednesday meeting.
- [palace] Tested the SQDMetal example with lambda/2 resonator for s21 (drivenmodal).

**Errors**
- [palace] In CircTransmon, when I try to runs21 only betwwen the resonator and the line, I can't see the resonance.
- [palace] in S21 dirvenmodal, `AdaptativeTol' doesn't work well if the resonance is very sharp. 


**ToDo**
- [palace] perform s21 simulation un resonator using drivenmodal avoiding `AdaptativeTol'`, it means, using brute force.
- [palace] Once fixed it, fix the problem with CircTransmon.


## 27/07/2026 - monday

**Done**
- [doc] I filled in factorial the schedule of the last week.
- [rfsoc] Biel is analyzing the data from the last experiment. We have relation between DAC output and the measured power in dBm, we need to add a relation between DAC units and ADC units.
- [palace] I tested the SQDMetal example with circTransmon for:
  - capacitance matrix
  - S21 (drivenmodal)
- [comsol_sim] First test running COMSOL from SQDMetal successfully.
- [rfsoc] Biel performed interpol2d to get the relation between DAC units to dBm depending the gain and the frequency. Also connect it with the ADC units. Now we have a relation between DAC units and ADC units.
- [doc] I sent an email to the Barcelona Welcome desk to get an appointment for TIE card. #onboarding

**Errors**
- [palace] S21 in drivenmodal is not working, I can't inject the drive in the right way. I need to check the documentation.
- [comsol_sim] Using COMSOL on my designs is not working, I need to check the documentation.
- [doc] I couldn't get an appoinment to TIE card.


**ToDo**
- [comsol_sim] Explore how it works
- [comsol_sim] Try on my designs and compare with palace results.
- [palace] fix problem with S21 in drivenmodal.
- [rfsoc] Measure full output from frequency analyzer to get a detailed relation between DAC units and dBm, also the bandwidth of the signal that we can inject.
- [doc] Check email response from Barcelona Welcome desk or call them to get an appointment for TIE card.

