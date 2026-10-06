# ultra_short_momentum

- assumptions: 14:35 tail_hunt buy=close*1.002; 09:35 morning_pulse exit=target/pulse(+3.5%)/stop(-2.5%); 14:30 force_close=next_close; OPTIMISTIC: pulse assumes any intraday high is caught at 09:35, target checked before stop on same bar; dd uses 10% position weight
- n=665 wr=49.3% pf=1.597 dd=-10.68
- oos_n=127 oos_wr=46.5% expired=0
- passed=True

- [OK] G-bt-2: n=665 need>=200
- [OK] G-bt-3: wr=49.3% need>=48%
- [OK] G-bt-4: pf=1.597 need>=1.2
- [OK] G-bt-5: dd=-10.68 need<=15.0
- [OK] G-bt-6: oos_n=127 oos_wr=46.5% floor=41.3%
- [OK] G-bt-7: detectors imported from stock_analyzer.detectors
