"""Market yapisi motoru (Pine indikatorunun Python portu).

run(o,h,l,c,tol) -> (olaylar, zigzag)
Olaylar: ('BOS'|'MSB'|'CH'|'iBOS', bar, yon), ('INIT', bar, yon),
('SH', bar, swingBar, fiyat), ('SL', bar, swingBar, fiyat)
"""


def run(o, h, l, c, tol=1e-6):
    n = len(o)
    leg = 0
    refH = refL = refBar = None
    # ham pivotlar: sadece ilk yon tespiti ve zincir uc noktasi onayi icin
    lastPH = lastPHbar = None
    phBroken = True
    lastPL = lastPLbar = None
    plBroken = True
    # ic yapi zinciri
    idir = 0
    eP = eBar = None
    eConf = False
    oP = oBar = None
    rLvl = rBar = None
    # swing motoru
    mdir = 0
    shP = shBar = None
    shAct = False
    slP = slBar = None
    slAct = False
    ePend = False
    candP = candBar = trkP = trkBar = None
    ev = []
    zz = []          # zigzag noktalari (zincir pivotlari)

    for i in range(n):
        hi, lo, op, cl = h[i], l[i], o[i], c[i]
        pvT = 0
        pvP = pvB = None

        # ---------- 1) referans mum motoru ----------
        if refBar is None:
            refH, refL, refBar = hi, lo, i
        else:
            hiBrk = hi - refH > tol
            loBrk = refL - lo > tol
            eqHi = abs(hi - refH) <= tol
            eqLo = abs(lo - refL) <= tol
            setRef = False
            if leg == 0:
                if hiBrk and not loBrk:
                    leg = 1
                    setRef = True
                elif loBrk and not hiBrk:
                    leg = -1
                    setRef = True
                elif hiBrk and loBrk:
                    setRef = True
            elif leg == 1:
                if hiBrk:
                    setRef = True
                elif loBrk:
                    if eqHi:
                        setRef = True
                    else:
                        pvT, pvP, pvB = 1, refH, refBar
                        leg = -1
                        setRef = True
            else:
                if loBrk:
                    setRef = True
                elif hiBrk:
                    if eqLo:
                        setRef = True
                    else:
                        pvT, pvP, pvB = -1, refL, refBar
                        leg = 1
                        setRef = True
            if setRef:
                refH, refL, refBar = hi, lo, i

        if pvT == 1:
            lastPH, lastPHbar, phBroken = pvP, pvB, False
        elif pvT == -1:
            lastPL, lastPLbar, plBroken = pvP, pvB, False

        # zincir ucu valid pullback ile onaylandi mi
        if idir != 0 and pvT == idir and abs(pvP - eP) <= tol:
            eConf = True
            ev.append(('LEVEL', i, idir))

        # ---------- 2) swing aday / izleyici ----------
        if mdir != 0:
            if mdir == 1:
                if ePend:
                    if hi - candP > tol:
                        candP, candBar, trkP, trkBar = hi, i, lo, i
                    elif lo < trkP:
                        trkP, trkBar = lo, i
                elif lo < trkP:
                    trkP, trkBar = lo, i
            else:
                if ePend:
                    if candP - lo > tol:
                        candP, candBar, trkP, trkBar = lo, i, hi, i
                    elif hi > trkP:
                        trkP, trkBar = hi, i
                elif hi > trkP:
                    trkP, trkBar = hi, i

        # ---------- 3) kirilimlar ----------
        order = -1 if cl >= op else 1
        for k in range(2):
            side = order if k == 0 else -order
            kind = None

            if idir == 0:
                if side == 1 and lastPH is not None and not phBroken and hi - lastPH > tol and lastPL is not None:
                    phBroken = True
                    kind = 'CH'
                    idir = 1
                    eP, eBar, eConf = hi, i, False
                    oP, oBar = eP, i
                    rLvl, rBar = lastPL, lastPLbar
                    zz.append(('L', rBar, rLvl))
                elif side == -1 and lastPL is not None and not plBroken and lastPL - lo > tol and lastPH is not None:
                    plBroken = True
                    kind = 'CH'
                    idir = -1
                    eP, eBar, eConf = lo, i, False
                    oP, oBar = eP, i
                    rLvl, rBar = lastPH, lastPHbar
                    zz.append(('H', rBar, rLvl))
            elif side == idir:
                ext = (hi - eP > tol) if side == 1 else (eP - lo > tol)
                if ext:
                    if eConf:
                        kind = 'iBOS'
                        zz.append(('H' if idir == 1 else 'L', eBar, eP))
                        zz.append(('L' if idir == 1 else 'H', oBar, oP))
                        rLvl, rBar = oP, oBar
                    eP = hi if side == 1 else lo
                    eBar, eConf = i, False
                    oP, oBar = eP, i
            else:
                if side == 1:
                    if hi > oP:
                        oP, oBar = hi, i
                    brk = hi - rLvl > tol
                else:
                    if lo < oP:
                        oP, oBar = lo, i
                    brk = rLvl - lo > tol
                if brk:
                    kind = 'CH'
                    zz.append(('H' if idir == 1 else 'L', eBar, eP))
                    rLvl, rBar = eP, eBar
                    idir = side
                    eP = hi if side == 1 else lo
                    eBar, eConf = i, False
                    oP, oBar = eP, i

            if kind is not None:
                ev.append((kind, i, side))
                if kind == 'iBOS' and mdir != 0 and side == -mdir and ePend:
                    if mdir == 1:
                        shP, shBar, shAct = candP, candBar, True
                        ev.append(('SH', i, shBar, shP))
                    else:
                        slP, slBar, slAct = candP, candBar, True
                        ev.append(('SL', i, slBar, slP))
                    ePend = False
                if mdir == 0:
                    if side == 1 and lastPL is not None:
                        mdir = 1
                        slP, slBar, slAct = lastPL, lastPLbar, True
                        ePend = True
                        candP, candBar, trkP, trkBar = hi, i, lo, i
                        ev.append(('INIT', i, 1))
                    elif side == -1 and lastPH is not None:
                        mdir = -1
                        shP, shBar, shAct = lastPH, lastPHbar, True
                        ePend = True
                        candP, candBar, trkP, trkBar = lo, i, hi, i
                        ev.append(('INIT', i, -1))

            if side == 1 and shAct and hi - shP > tol:
                isBos = mdir == 1
                ev.append(('BOS' if isBos else 'MSB', i, 1))
                shAct = False
                if isBos:
                    slP, slBar, slAct = trkP, trkBar, True
                    ev.append(('SL', i, slBar, slP))
                else:
                    if ePend:
                        slP, slBar, slAct = candP, candBar, True
                        ev.append(('SL', i, slBar, slP))
                    mdir = 1
                ePend = True
                candP, candBar, trkP, trkBar = hi, i, lo, i

            if side == -1 and slAct and slP - lo > tol:
                isBos = mdir == -1
                ev.append(('BOS' if isBos else 'MSB', i, -1))
                slAct = False
                if isBos:
                    shP, shBar, shAct = trkP, trkBar, True
                    ev.append(('SH', i, shBar, shP))
                else:
                    if ePend:
                        shP, shBar, shAct = candP, candBar, True
                        ev.append(('SH', i, shBar, shP))
                    mdir = -1
                ePend = True
                candP, candBar, trkP, trkBar = lo, i, hi, i


    return ev, zz
