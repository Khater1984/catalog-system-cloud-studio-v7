# Ground truth typed by hand from the catalog text (page -> code -> {attr: value})
EPG="EP Grease, NLGI 0 (Normal) / NLGI 00 (Cold)"
PS="380 V AC, 50 Hz, 3-phase"
def gq(p,code,kw,rate,rng,blade,grease,nw,gw,dim,pack):
    return (p,code,{"Motor power":kw,"Power supply":PS,"Motor speed":"2800 rpm","Cutting rate":rate,"Cutting range":rng,"Blade dimensions":blade,"Recommended grease":EPG,"Grease amount":grease,"N.W":nw,"G.W":gw,"Dimensions (L × W × H)":dim,"Packing size (L × W × H)":pack})
SP="5 central shafts, 4 steel sleeves, 1 bending plate"
def gw(p,code,cap,kw,disc,dim,pack,nw,gw_,grease="15 kg",ps=PS,spare=SP,ctrl=None):
    d={"Bending capacity":cap,"Motor power":kw,"Power supply":ps,"Electrical brand":"CHINT/DELIXI","Spare parts":spare,"Grease amount":grease,"Recommended grease":EPG,"Bending disc size":disc,"Bending speed":"5–10 rpm","Dimensions (L × W × H)":dim,"Packing size (L × W × H)":pack,"N.W":nw,"G.W":gw_}
    if ctrl: d["Control type"]=ctrl
    return (p,code,d)
def gf(p,code,cap,rate,kw,dim,wt):
    return (p,code,{"Bending capacity":cap,"Bending rate":rate,"Max. bending angle":"0–180° adjustable","Motor power":kw,"Power supply":"Three-phase, 220 / 380 / 415 V AC","Frequency":"50/60 Hz optional","Emergency stop":"Standard","Dimensions (L × W × H)":dim,"Weight":wt})
def gwh(p,code,rng,arc,speed,kw,dim,wt):
    return (p,code,{"Bending range":rng,"Bending arc range":arc,"Bending speed":speed,"Motor power":kw,"Motor speed":"1440 r/min","Power supply":"380 V AC three-phase","Frequency":"50/60 Hz optional","Emergency stop":"Standard","Working rollers":"Hardened steel rollers","Applicable material":"Deformed bar / Steel bar","Dimensions (L×W×H)":dim,"Weight":wt})
TRUTH=[
 gq(1,"GQ40","3 kW","32 cuts/min","Round bar: 4–32 mm / Deformed bar: 4–28 mm","83 × 83 × 26 mm","8 kg","315 kg","335 kg","1320 × 450 × 770 mm","1420 × 500 × 820 mm"),
 gq(2,"GQ42","3 kW","32 cuts/min","Round bar: 4–36 mm / Deformed bar: 4–32 mm","83 × 83 × 26 mm","8–10 kg","350 kg","370 kg","1320 × 500 × 650 mm","1420 × 550 × 700 mm"),
 gq(3,"GQ50","4 kW","28 cuts/min","Round bar: 4–40 mm / Deformed bar: 4–36 mm","90 × 90 × 26 mm","10 kg","470 kg","490 kg","1470 × 550 × 820 mm","1570 × 600 × 870 mm"),
 gq(4,"GQ60","5.5 kW","32 cuts/min","Round bar: 4–50 mm / Deformed bar: 4–42 mm","100 × 100 × 28 mm","10 kg","660 kg","680 kg","1460 × 600 × 870 mm","1560 × 650 × 920 mm"),
 gq(5,"GQ70","11 kW","32 cuts/min","Round bar: 4–50 mm / Deformed bar: 4–50 mm","110 × 90 × 30 mm","13 kg","1124 kg","1144 kg","1600 × 900 × 1050 mm","1700 × 950 × 1100 mm"),
 gq(6,"GQ42","3 kW","32 cuts/min","Round bar: 6–40 mm / Deformed bar: 32 mm","83 × 83 × 26 mm","8 kg","335 kg","355 kg","1270 × 480 × 680 mm","1370 × 530 × 730 mm"),
 gq(7,"GQ50","4 kW","32 cuts/min","Round bar: 6–45 mm / Deformed bar: Max. 40 mm","90 × 90 × 26 mm","10 kg","505 kg","525 kg","1410 × 530 × 880 mm","1510 × 580 × 930 mm"),
 gq(8,"GQ46","3 kW","32 cuts/min","Round bar: 6–40 mm / Deformed bar: 34 mm","83 × 83 × 26 mm","10 kg","430 kg","455 kg","1420 × 450 × 750 mm","1520 × 500 × 800 mm"),
 gw(9,"GW42J","Round bar: 6–36 mm / Deformed bar: 6–32 mm","3 kW (electronic brake motor)","Ø365 × 45 mm","870 × 760 × 850 mm","920 × 810 × 950 mm","310 kg","350 kg"),
 gw(10,"GW50C","Round bar: 6–42 mm / Deformed bar: 6–36 mm","4 kW (electronic brake motor)","Ø405 mm×50 mm","970 × 770 × 840 mm","1020 × 830 × 940 mm","380 kg","420 kg"),
 gw(11,"GW42A","Round bar: 6–36 mm / Deformed bar: 6–32 mm","3 kW (electronic brake motor)","Ø365 mm×50 mm","870 × 760 × 850 mm","920 × 810 × 950 mm","310 kg","350 kg"),
 gw(12,"GW50C","Round bar: 6–42 mm / Deformed bar: 6–36 mm","4 kW (electronic brake motor)","Ø405 mm×50 mm","970 × 770 × 840 mm","1020 × 830 × 940 mm","380 kg","420 kg"),
 gw(13,"GW40E","Round bar 6–40 mm, Deformed bar 6–32 mm","3 kW (electronic brake motor)","Ø365 mm × 45 mm","800 × 740 × 900 mm","850 × 790 × 1000 mm","350 kg","390 kg"),
 gw(14,"GW50E","Round bar: 6–42 mm / Deformed bar: 6–36 mm","4 kW (electronic brake motor)","Ø405 mm×50 mm","950 × 770 × 910 mm","1000 × 820 × 1010 mm","400 kg","440 kg",spare=SP+"."),
 gw(15,"GW46","Round bar 6–36 mm, Deformed bar 6–32 mm","3 kW-4P(brake)","Ø380 mm × 43 mm","940 × 890 × 900 mm","990 × 940 × 1000 mm","350 kg","390 kg",ps="220 V AC, three-phase 380 V AC 415 V AC",ctrl="Semi-automatic"),
 gw(16,"GW60","Round bar 6–50 mm, Deformed bar 6–45 mm","9 kW","Ø420 mm","1160 × 960 × 920 mm","1210 × 1010 × 1020 mm","494 kg","534 kg",grease="18 kg",ctrl="CNC"),
 gw(17,"GW70","Round bar 6–65 mm, Deformed bar 6–60 mm","11 kW","Ø550 mm","1210 × 1070 × 1010 mm","1260 × 1120 × 1110 mm","785 kg","825 kg",ctrl="CNC"),
 gw(18,"GW40","Round bar 6–32 mm, Deformed bar 6–28 mm","3 kW","Ø335 mm × 36 mm","900 × 780 × 780 mm","950 × 830 × 880 mm","210 kg","250 kg",ctrl="CNC/MANUAL"),
 gw(19,"GW50","Round bar: 6–42 mm / Deformed bar: 6–36 mm","4 kW","Ø380 mm × 43 mm","940 × 810 × 840 mm","990 × 860 × 940 mm","350 kg","390 kg",ctrl="CNC/MANUAL"),
 gf(20,"GF20","Round bar Ø6–Ø20 mm / Deformed bar Ø6–Ø18 mm","28 pcs/min","2.2 kW","800 × 540 × 840 mm","88 kg"),
 gf(21,"GF25","Round bar Ø6–Ø25 mm / Deformed bar Ø6–Ø22 mm","28 pcs/min","3 kW","850 × 650 × 870 mm","125 kg"),
 gf(22,"GF32","Round bar Ø6–Ø28 mm / Deformed bar Ø6–Ø25 mm","28 pcs/min","3 kW","780 × 740 × 850 mm","195 kg"),
 gf(23,"GF36","Round bar Ø6–Ø32 mm / Deformed bar Ø6–Ø28 mm","25 pcs/min","3 kW","880 × 700 × 830 mm","230 kg"),
 gwh(24,"GWH10","Ø6–12 mm (Deformed bar)","≥10 cm","15 m/min","3 kW","900 × 730 × 880 mm","180 kg"),
 gwh(25,"GWH24","Ø6–12 mm (Deformed bar)","≥60 cm","25–28 m/min","3 kW","900 × 730 × 850 mm","195 kg"),
 gwh(26,"GWH32","Ø16–28 mm (Deformed bar)","≥100 cm","25–28 m/min","3 kW","900 × 800 × 920 mm","230 kg"),
 gwh(27,"GWH36","Ø22–32 mm (Deformed bar)","≥100 cm","25–28 m/min","3 kW","930 × 850 × 970 mm","300 kg"),
 gwh(28,"GWH50","Ø36–50 mm (Deformed bar)","≥150 cm","25–28 m/min","7.5 kW","1120 × 1050 × 1000 mm","530 kg"),
 (29,"GWD32",{"Bending capacity":"Round steel ≤ 32 mm Deformed bar ≤ 28 mm","Power":"CF180 diesel engine RK80D1 20 HP","Engine speed":"1500 rpm","Dimensions":"1300 × 1000 × 890 mm","Weight":"700 kg"}),
 (29,"GWD40",{"Bending capacity":"Round steel ≤ 40 mm deformed steel ≤ 32 mm","Power":"CF188 diesel engine RK90D1 20 HP","Engine speed":"1500 rpm","Dimensions":"1300 × 1000 × 890 mm","Weight":"810 kg"}),
 (30,"GQD32",{"Cutting range":"Round bar:4–32 mm Deformed bar:4–28 mm","Cutting rate":"32 cuts/min","Power":"CF180 diesel RK80D1 20 HP","Engine speed":"2800 rpm","Blade dimensions":"83 × 83 × 26 mm","Dimensions":"1600 × 550 × 800 mm","Weight":"515 kg"}),
 (30,"GQD40",{"Cutting range":"Round bar:4–40 mm Deformed bar:4–32 mm","Cutting rate":"32 cuts/min","Power":"CF188 diesel RK90D1 20 HP","Engine speed":"2800 rpm","Blade dimensions":"83 × 83 × 26 mm","Dimensions":"1600 × 550 × 800 mm","Weight":"615 kg"}),
]
GRID=["RC-16","RC-18","NRC-20","RC-20","RC-22","RC-25","RC-32","RD-12","RC-16B","RC-20B","RB-16","NRB-22","NRB-25","NRB-32","MHP-20","MHP-25","BT-590","BT-890","RC-18B","BC-300","FA255","FA355","RB-25","RB-32","RBC-25","RBC-32"]
