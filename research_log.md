# Research Log

## 04/08/2026 - tuesday

**Done**
- [qm_learning] I read the documentation of Quantum Machine User https://docs.quantum-machines.co/latest/

**Errors** 
- 

**ToDo**
- [qm_learning] I will try to run the example of Quantum Machine.

## 03/08/2026 - monday

**Done**
- LinkedIn post about end of "licenciatura". #personal
- Paid to lawyer Luciano for farm lease. #personal
- Sent email to fly refund. #personal
- I chose a work computer, I will use the one from the lab. #personal
- I filled in factorial the schedule of the last week. #doc
- I sent an email to give another drive license document. #personal

**Errors** 
- 

**ToDo**
- 

## 31/07/2026 - friday

**Done**
- [comsol_sim] Run simulation of S21 in PIC

**Errors** 
- 

**ToDo**
- 

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

