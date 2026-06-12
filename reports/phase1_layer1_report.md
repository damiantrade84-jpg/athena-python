## Exit gate

PASS if candidates ≥ 500 per family-horizon AND mean net_R ≥ -0.15R.

| family / horizon | candidates | mean net_R | gate |
|------------------|------------|------------|------|
| commodity / intraday | 9735 | -0.0568 | **PASS** |
| commodity / swing | 842 | -0.0139 | **PASS** |
| crypto / intraday | 20587 | -0.0903 | **PASS** |
| crypto / swing | 1566 | -0.0897 | **PASS** |
| equity / intraday | 2955 | -0.0803 | **PASS** |
| equity / swing | 567 | -0.0324 | **PASS** |
| forex / intraday | 30181 | -0.1639 | **FAIL** |
| forex / swing | 2547 | -0.0716 | **PASS** |
| index_etf / intraday | 11324 | -0.1380 | **PASS** |
| index_etf / swing | 1248 | -0.0198 | **PASS** |

# ASE Phase 1 — Layer 1 event report

## commodity / intraday

- candidates: 9735
- mean net_R: -0.0568
- median net_R: -0.0467
- win rate: 48.44%

### Exit reason mix

- vertical: 4051
- target: 2924
- stop: 2754
- adverse_first: 6

### Top / bottom instruments (mean net_R)

- WHEAT: -0.0977
- ALUMINIUM: -0.0932
- GASOLINE: -0.0893
- SUGAR: -0.0765
- CL=F: -0.0627
- SUGAR: -0.0765
- CL=F: -0.0627
- GC=F: -0.0401
- SI=F: -0.0269
- COCOA: 0.0472

---

## commodity / swing

- candidates: 842
- mean net_R: -0.0139
- median net_R: 0.1118
- win rate: 53.09%

### Exit reason mix

- target: 308
- vertical: 282
- stop: 252

### Top / bottom instruments (mean net_R)

- CL=F: -0.1571
- WHEAT: -0.0715
- SUGAR: -0.0697
- SI=F: -0.0262
- COCOA: 0.0206
- SI=F: -0.0262
- COCOA: 0.0206
- GASOLINE: 0.0318
- GC=F: 0.0421
- ALUMINIUM: 0.0822

---

## crypto / intraday

- candidates: 20587
- mean net_R: -0.0903
- median net_R: -0.1237
- win rate: 45.98%

### Exit reason mix

- vertical: 8146
- target: 6694
- stop: 5743
- adverse_first: 4

### Top / bottom instruments (mean net_R)

- TRXUSDT: -0.1698
- LTCUSDT: -0.1543
- ETHUSDT: -0.1003
- APTUSDT: -0.0954
- DOTUSDT: -0.0907
- DOGEUSDT: -0.0768
- BNBUSDT: -0.0715
- POLUSDT: -0.0713
- SOLUSDT: -0.0467
- XRPUSDT: -0.0377

---

## crypto / swing

- candidates: 1566
- mean net_R: -0.0897
- median net_R: -0.1152
- win rate: 46.10%

### Exit reason mix

- vertical: 585
- target: 496
- stop: 484
- adverse_first: 1

### Top / bottom instruments (mean net_R)

- LTCUSDT: -0.2332
- BNBUSDT: -0.1973
- ADAUSDT: -0.1619
- BTCUSDT: -0.1450
- SOLUSDT: -0.1183
- ETHUSDT: -0.0686
- XRPUSDT: -0.0462
- APTUSDT: -0.0273
- POLUSDT: -0.0035
- TRXUSDT: 0.0650

---

## equity / intraday

- candidates: 2955
- mean net_R: -0.0803
- median net_R: -0.0731
- win rate: 46.53%

### Exit reason mix

- vertical: 1340
- stop: 868
- target: 747

### Top / bottom instruments (mean net_R)

- AMZN: -0.2706
- XOM: -0.0616
- TSLA: -0.0455
- MSFT: -0.0382
- NVDA: -0.0135
- XOM: -0.0616
- TSLA: -0.0455
- MSFT: -0.0382
- NVDA: -0.0135
- AMD: -0.0054

---

## equity / swing

- candidates: 567
- mean net_R: -0.0324
- median net_R: -0.0249
- win rate: 49.38%

### Exit reason mix

- vertical: 227
- target: 172
- stop: 168

### Top / bottom instruments (mean net_R)

- XOM: -0.1930
- AMZN: -0.0338
- TSLA: -0.0195
- NVDA: -0.0064
- MSFT: 0.0059
- AMZN: -0.0338
- TSLA: -0.0195
- NVDA: -0.0064
- MSFT: 0.0059
- AMD: 0.0268

---

## forex / intraday

- candidates: 30181
- mean net_R: -0.1639
- median net_R: -0.1615
- win rate: 44.29%

### Exit reason mix

- vertical: 12481
- stop: 8982
- target: 8635
- adverse_first: 83

### Top / bottom instruments (mean net_R)

- USDSGD: -0.2865
- AUDNZD: -0.2740
- EURGBP: -0.2644
- EURCHF: -0.2483
- EURJPY: -0.1907
- USDCHF: -0.1057
- AUDUSD: -0.1048
- USDBRL: -0.1043
- USDCAD: -0.0921
- NZDUSD: -0.0874

---

## forex / swing

- candidates: 2547
- mean net_R: -0.0716
- median net_R: -0.0159
- win rate: 49.20%

### Exit reason mix

- vertical: 1015
- target: 774
- stop: 749
- adverse_first: 9

### Top / bottom instruments (mean net_R)

- EURCHF: -0.2837
- EURGBP: -0.2207
- AUDCHF: -0.1543
- USDCHF: -0.0980
- USDBRL: -0.0905
- AUDUSD: -0.0116
- AUDJPY: -0.0050
- USDZAR: -0.0042
- EURJPY: 0.0310
- USDJPY: 0.0929

---

## index_etf / intraday

- candidates: 11324
- mean net_R: -0.1380
- median net_R: -0.1698
- win rate: 44.12%

### Exit reason mix

- vertical: 4805
- stop: 3664
- target: 2851
- adverse_first: 4

### Top / bottom instruments (mean net_R)

- ^GDAXI: -0.3484
- ^GSPC: -0.2746
- EURX: -0.1950
- USO: -0.0970
- ^N225: -0.0787
- ^DJI: -0.0469
- NAS100: -0.0347
- EEM: -0.0144
- SPY: -0.0024
- QQQ: 0.0024

---

## index_etf / swing

- candidates: 1248
- mean net_R: -0.0198
- median net_R: 0.0684
- win rate: 52.24%

### Exit reason mix

- vertical: 484
- stop: 383
- target: 381

### Top / bottom instruments (mean net_R)

- EURX: -0.1856
- USO: -0.0938
- ^GDAXI: -0.0935
- EEM: -0.0310
- NAS100: -0.0061
- QQQ: 0.0265
- ^N225: 0.0276
- ^DJI: 0.0349
- SPY: 0.0538
- ^GSPC: 0.0800

---

