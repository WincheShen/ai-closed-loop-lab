# ultra_short_momentum

- assumptions: 14:35 tail_hunt buy=close*1.002; 09:35 morning_pulse exit=target/pulse(+3%)/stop(-2.5%); 14:30 force_close=next_close
- n=665 wr=56.7% pf=1.903 dd=-10.243
- oos_n=127 oos_wr=55.9% expired=0
- passed=True

- [OK] G-bt-2: n=665 need>=200
- [OK] G-bt-3: wr=56.7% need>=48%
- [OK] G-bt-4: pf=1.903 need>=1.2
- [OK] G-bt-5: dd=-10.243 need<=15.0
- [OK] G-bt-6: oos_n=127 oos_wr=55.9% floor=48.7%
- [OK] G-bt-7: detectors imported from stock_analyzer.detectors
