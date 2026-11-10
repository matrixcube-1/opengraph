//! État de marché forward-fill — port de `poc_engine.Engine` (R7).
//!
//! Invariants repris du POC :
//! - Curseur `pos` par symbole **rembobinable** : `_advance` retombe sur une
//!   dichotomie quand `ts[pos] > t` (rejouabilité inter-trials, R7-T4/T6).
//! - `tick`/`sync` ne publient que si `ts[j] <= t` — jamais de quote future.
//! - `quote_at` est sans état (dichotomie pure).
//! - `usd_price` = prix USD de chaque nœud au mid courant des paires USD.
//!
//! Convention A : seuls les symboles FX sont chargés (pas de bouts).

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};

use crate::fees::comm_usd;
use crate::quote::Quote;
use crate::universe::{pair_nodes, CURR, FX};

/// Sentinelle « jamais vu » (`-inf` du POC : `-1e18`).
pub const NO_TICK: i64 = i64::MIN;

/// Série de ticks ordonnée d'un symbole — layout SoA miroir du POC numpy.
/// Invariants : `ts` croissant non strict ; `bid <= ask`.
pub struct SymbolSeries {
    pub sym: &'static str,
    pub ts: Vec<i64>,
    pub bid: Vec<f64>,
    pub ask: Vec<f64>,
    /// Volume exibido por lado (milhões de devise de base, Dukas).
    /// `avol` = lado ask (comprado), `bvol` = lado bid (vendido) — a garde
    /// volume lê o lado que a ordem atinge; `quote_at` expõe `max(avol,bvol)`.
    pub avol: Vec<f64>,
    pub bvol: Vec<f64>,
}

impl SymbolSeries {
    pub fn len(&self) -> usize {
        self.ts.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ts.is_empty()
    }
}

/// Médiane d'un échantillon (sémantique `np.median`).
pub fn median(v: &mut [f64]) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = v.len();
    if n % 2 == 1 {
        v[n / 2]
    } else {
        0.5 * (v[n / 2 - 1] + v[n / 2])
    }
}

/// f64 ordonné pour les tas (NaN traité égal — les spreads sont finis en pratique).
#[derive(Clone, Copy, PartialEq)]
struct OrdF64(f64);
impl Eq for OrdF64 {}
impl PartialOrd for OrdF64 {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}
impl Ord for OrdF64 {
    fn cmp(&self, o: &Self) -> Ordering {
        self.0.partial_cmp(&o.0).unwrap_or(Ordering::Equal)
    }
}

/// Médiane incrémentale exacte (deux tas) — même sémantique que [`median`].
/// Maintient `med` à jour en ingestion live sans recalcul O(n log n) par tick.
#[derive(Default)]
pub struct SpreadMedian {
    lo: BinaryHeap<OrdF64>,                    // max-heap : moitié basse
    hi: BinaryHeap<std::cmp::Reverse<OrdF64>>, // min-heap : moitié haute
}
impl SpreadMedian {
    fn push(&mut self, x: f64) {
        if !x.is_finite() {
            return; // audit OG2-D1 : NaN/inf corrompraient la médiane
        }
        if self.lo.peek().is_none_or(|m| x <= m.0) {
            self.lo.push(OrdF64(x));
        } else {
            self.hi.push(std::cmp::Reverse(OrdF64(x)));
        }
        if self.lo.len() > self.hi.len() + 1 {
            let v = self.lo.pop().unwrap();
            self.hi.push(std::cmp::Reverse(v));
        } else if self.hi.len() > self.lo.len() {
            let std::cmp::Reverse(v) = self.hi.pop().unwrap();
            self.lo.push(v);
        }
    }
    fn median(&self) -> f64 {
        if self.lo.is_empty() {
            return f64::NAN;
        }
        if self.lo.len() > self.hi.len() {
            self.lo.peek().unwrap().0
        } else {
            0.5 * (self.lo.peek().unwrap().0 + self.hi.peek().unwrap().0 .0)
        }
    }
}

/// État de marché : séries + curseurs rembobinables + quotes courantes.
pub struct Engine {
    /// Symboles chargés, triés (`slist` du POC).
    pub syms: Vec<&'static str>,
    /// sym -> indice dans `syms`/`data`/état.
    pub sidx: HashMap<&'static str, usize>,
    pub data: Vec<SymbolSeries>,
    /// Curseur de lecture par symbole — rembobinable.
    pos: Vec<i64>,
    /// Quote publiée courante (NaN si jamais vue).
    pub bid: Vec<f64>,
    pub ask: Vec<f64>,
    /// Horodatage du dernier tick publié (`NO_TICK` si jamais vu).
    pub last_t: Vec<i64>,
    /// Spread médian par symbole (`MED` du POC).
    pub med: Vec<f64>,
    pub is_fx: Vec<bool>,
    /// edge FX (0..28) -> indice série chargée (`E_S` du POC).
    pub fx_si: [Option<usize>; 28],
    /// Paire USD utilisée par `usd_price` : (série, direct = cUSD vs USDc).
    up_pair: [Option<(usize, bool)>; 8],
    /// Accumulateurs de spread pour la médiane incrémentale (ingestion live).
    med_acc: Vec<SpreadMedian>,
    /// Événements FX triés `(ts, si)` pour le scan événementiel.
    pub ev: Vec<(i64, usize)>,
    /// Commission par côté et par 100 000 USD de notionnel base.
    /// Défaut `comm_usd()` (4,00 — max observé fills demo IC) ; ECN 2 $ A/R
    /// → 1,00. Un seul paramètre lu par éval, exécution ET liquidation —
    /// cohérence comptable garantie par construction.
    pub comm_per_side: f64,
}

impl Engine {
    /// Construit l'état à partir de séries chargées (déjà triées par ts).
    /// Les séries vides sont écartées (comme `load_sym -> None` du POC).
    pub fn new(series: Vec<SymbolSeries>) -> Self {
        Self::build(series, true)
    }

    /// Moteur « live » : les 28 slots FX existent même sans historique —
    /// les ticks arrivent par [`Engine::ingest`].
    pub fn live() -> Self {
        let series = FX
            .iter()
            .map(|&sym| SymbolSeries {
                sym,
                ts: Vec::new(),
                bid: Vec::new(),
                ask: Vec::new(),
                avol: Vec::new(),
                bvol: Vec::new(),
            })
            .collect();
        Self::build(series, false)
    }

    fn build(mut series: Vec<SymbolSeries>, drop_empty: bool) -> Self {
        if drop_empty {
            series.retain(|s| !s.ts.is_empty());
        }
        series.sort_by(|a, b| a.sym.cmp(b.sym));
        let n = series.len();
        let mut sidx = HashMap::with_capacity(n);
        let mut med = Vec::with_capacity(n);
        let mut med_acc = Vec::with_capacity(n);
        let mut is_fx = Vec::with_capacity(n);
        for (i, s) in series.iter().enumerate() {
            sidx.insert(s.sym, i);
            let mut acc = SpreadMedian::default();
            for (a, b) in s.ask.iter().zip(s.bid.iter()) {
                acc.push(a - b);
            }
            med.push(acc.median());
            med_acc.push(acc);
            is_fx.push(FX.contains(&s.sym));
        }
        let mut fx_si = [None; 28];
        for (e, slot) in fx_si.iter_mut().enumerate() {
            *slot = sidx.get(FX[e]).copied();
        }
        // paire USD de référence par devise : "cUSD" (direct) ou "USDc" (inverse)
        let mut up_pair = [None; 8];
        for (i, &c) in CURR.iter().enumerate() {
            if c == "USD" {
                continue;
            }
            let direct = format!("{c}USD");
            let inverse = format!("USD{c}");
            if let Some(&si) = sidx.get(direct.as_str()) {
                up_pair[i] = Some((si, true));
            } else if let Some(&si) = sidx.get(inverse.as_str()) {
                up_pair[i] = Some((si, false));
            }
        }
        let mut ev: Vec<(i64, usize)> = Vec::new();
        for &si in fx_si.iter().flatten() {
            for &t in &series[si].ts {
                ev.push((t, si));
            }
        }
        ev.sort_by_key(|&(t, _)| t); // stable : même tie-break que np.argsort
        Self {
            syms: series.iter().map(|s| s.sym).collect(),
            sidx,
            data: series,
            pos: vec![0; n],
            bid: vec![f64::NAN; n],
            ask: vec![f64::NAN; n],
            last_t: vec![NO_TICK; n],
            med,
            med_acc,
            is_fx,
            fx_si,
            up_pair,
            ev,
            comm_per_side: comm_usd(),
        }
    }

    /// `_advance` : avance/rembobine le curseur vers le dernier tick <= t.
    /// Retourne l'indice du tick, -1 si aucun tick <= t.
    fn advance(&mut self, si: usize, t: i64) -> i64 {
        let ts = &self.data[si].ts;
        let mut j = self.pos[si];
        if j < 0 || j as usize >= ts.len() || ts[j as usize] > t {
            // rembobinage (replay inter-trials) : dernier index <= t
            j = ts.partition_point(|&x| x <= t) as i64 - 1;
        } else {
            while j as usize + 1 < ts.len() && ts[j as usize + 1] <= t {
                j += 1;
            }
        }
        self.pos[si] = j;
        j
    }

    /// Met à jour l'état avec le tick de `si` à t — jamais de quote future.
    pub fn tick(&mut self, si: usize, t: i64) {
        let j = self.advance(si, t);
        self.publish(si, j, t);
    }

    /// Ingestion live (Strategy / alimentation temps réel) : ajoute le tick
    /// à la série `si`, met à jour la médiane incrémentale du spread et
    /// publie l'état — jamais de quote future.
    ///
    /// Précondition : flux par instrument non décroissant en `ts_ms`. Un
    /// `ts_ms` égal au dernier **remplace** la quote (ré-émission même
    /// instant) ; le spread est poussé dans la médiane à **chaque** tick
    /// reçu — miroir exact du préchargé, où les deux entrées à ts égal
    /// existent toutes les deux dans la série.
    ///
    /// Quotes rejetées en entrée (audit live) : bid/ask non finis ou
    /// croisés (`ask < bid`). Une quote croisée publierait un spread
    /// négatif — `fx_ok` le laisserait passer (spread < cap x médian) et
    /// eval calculerait un edge fantôme exécutable ; la médiane serait
    /// corrompue en plus. Le client filtre en amont — défense en
    /// profondeur ici : ni état publié ni poussée dans la médiane.
    pub fn ingest(&mut self, si: usize, q: Quote) {
        if !(q.bid.is_finite() && q.ask.is_finite()) || q.ask < q.bid {
            return;
        }
        {
            let s = &mut self.data[si];
            if s.ts.last() == Some(&q.ts_ms) {
                let n = s.ts.len() - 1;
                s.bid[n] = q.bid;
                s.ask[n] = q.ask;
                s.avol[n] = q.vol;
                s.bvol[n] = q.vol;
            } else {
                if !s.ts.last().is_none_or(|&last| q.ts_ms > last) {
                    // tick hors ordre : rejeté aussi en release — l'insérer
                    // casserait `partition_point` (sync / quote_at).
                    return;
                }
                s.ts.push(q.ts_ms);
                s.bid.push(q.bid);
                s.ask.push(q.ask);
                s.avol.push(q.vol);
                s.bvol.push(q.vol);
            }
        }
        self.med_acc[si].push(q.ask - q.bid);
        self.med[si] = self.med_acc[si].median();
        let j = self.data[si].ts.len() as i64 - 1;
        self.pos[si] = j;
        self.publish(si, j, q.ts_ms);
    }

    /// Publie l'état à l'instant t pour tous les symboles (forward-fill).
    pub fn sync(&mut self, t: i64) {
        for si in 0..self.data.len() {
            let j = self.advance(si, t);
            self.publish(si, j, t);
        }
    }

    fn publish(&mut self, si: usize, j: i64, t: i64) {
        let s = &self.data[si];
        if j >= 0 && s.ts[j as usize] <= t {
            let j = j as usize;
            self.bid[si] = s.bid[j];
            self.ask[si] = s.ask[j];
            self.last_t[si] = s.ts[j];
        } else {
            self.bid[si] = f64::NAN;
            self.ask[si] = f64::NAN;
            self.last_t[si] = NO_TICK;
        }
    }

    /// Dernier tick `<= t` — sans effet de bord.
    /// Retourne `(ts, bid, ask, vol)` ou None si aucun tick <= t.
    pub fn quote_at(&self, si: usize, t: i64) -> Option<(i64, f64, f64, f64)> {
        self.quote_at_f(si, t as f64)
            .map(|(ts, b, a, av, bv)| (ts, b, a, av.max(bv)))
    }

    /// `quote_at` à t flottant (t_fill = t_prev + lat_ms est non entier).
    pub(crate) fn quote_at_f(&self, si: usize, t: f64) -> Option<(i64, f64, f64, f64, f64)> {
        let s = &self.data[si];
        let j = s.ts.partition_point(|&x| (x as f64) <= t) as i64 - 1;
        if j < 0 {
            None
        } else {
            let j = j as usize;
            Some((s.ts[j], s.bid[j], s.ask[j], s.avol[j], s.bvol[j]))
        }
    }

    /// Quote valide pour l'évaluation : publiée ET pas future ET fraîche.
    /// Fraîcheur bilatérale R7 : `last_t <= t` et `t - last_t <= fresh_ms`.
    #[inline]
    pub fn ok(&self, si: usize, t: i64, fresh_ms: f64) -> bool {
        self.last_t[si] != NO_TICK
            && self.last_t[si] <= t
            && (t - self.last_t[si]) as f64 <= fresh_ms
    }

    /// Prix USD courant de chaque nœud (mid) ; NaN si paire absente/non ok.
    /// `ok[i]` masque les symboles non valides pour cette conversion.
    pub fn usd_price(&self, ok: &[bool]) -> [f64; 8] {
        let mut up = [f64::NAN; 8];
        up[0] = 1.0;
        for (i, &c) in CURR.iter().enumerate() {
            if c == "USD" {
                continue;
            }
            let Some((si, direct)) = self.up_pair[i] else {
                continue;
            };
            if !ok[si] {
                continue;
            }
            let m = 0.5 * (self.bid[si] + self.ask[si]);
            up[i] = if direct { m } else { 1.0 / m };
        }
        up
    }

    /// Prix USD EXÉCUTABLE de liquidation par nœud (Convention A stricte) :
    /// `cUSD` -> bid (on vend la devise) ; `USDc` -> 1/ask (on achète USD).
    /// NaN si paire absente ou non `ok`. USD = 1.
    pub fn usd_liq_price(&self, ok: &[bool]) -> [f64; 8] {
        let mut up = [f64::NAN; 8];
        up[0] = 1.0;
        for (i, &c) in CURR.iter().enumerate() {
            if c == "USD" {
                continue;
            }
            let Some((si, direct)) = self.up_pair[i] else {
                continue;
            };
            if !ok[si] {
                continue;
            }
            up[i] = if direct {
                self.bid[si]
            } else {
                1.0 / self.ask[si]
            };
        }
        up
    }

    /// Commission de la jambe de liquidation nœud -> USD (modèle COMM/100k
    /// de la devise de base de la paire USD). `cUSD` -> COMM/mid ;
    /// `USDc` -> COMM ; USD -> 0. NaN si paire absente ou non `ok`.
    pub fn usd_liq_comm(&self, ok: &[bool]) -> [f64; 8] {
        let mut c = [f64::NAN; 8];
        c[0] = 0.0;
        for (i, &cur) in CURR.iter().enumerate() {
            if cur == "USD" {
                continue;
            }
            let Some((si, direct)) = self.up_pair[i] else {
                continue;
            };
            if !ok[si] {
                continue;
            }
            c[i] = if direct {
                self.comm_per_side / (0.5 * (self.bid[si] + self.ask[si]))
            } else {
                self.comm_per_side
            };
        }
        c
    }

    /// Indices de devises d'une arête : `(base, quote)`.
    pub fn edge_nodes(edge: u8) -> (u8, u8) {
        pair_nodes(edge as usize)
    }

    /// Nœud "autre côté" de l'arête depuis `node` (panique si non incident).
    pub fn edge_other(edge: u8, node: u8) -> u8 {
        let (a, b) = pair_nodes(edge as usize);
        if node == a {
            b
        } else {
            a
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn series(sym: &'static str, ticks: &[(i64, f64, f64)]) -> SymbolSeries {
        SymbolSeries {
            sym,
            ts: ticks.iter().map(|t| t.0).collect(),
            bid: ticks.iter().map(|t| t.1).collect(),
            ask: ticks.iter().map(|t| t.2).collect(),
            avol: ticks.iter().map(|_| 1.0).collect(),
            bvol: ticks.iter().map(|_| 1.0).collect(),
        }
    }

    /// T1 : sync avant le 1er tick -> aucune quote exposée (pas de futur).
    #[test]
    fn t1_no_future_quote_on_early_sync() {
        let mut e = Engine::new(vec![
            series(
                "EURUSD",
                &[(1_000, 1.0999, 1.1001), (2_000, 1.1002, 1.1004)],
            ),
            series("USDCAD", &[(5_000, 1.3499, 1.3501)]),
        ]);
        e.sync(500);
        let i = e.sidx["EURUSD"];
        assert!(e.bid[i].is_nan());
        assert_eq!(e.last_t[i], NO_TICK);
        // à 1 500 : seul le tick 1 000 est exposé
        e.sync(1_500);
        assert_eq!(e.last_t[i], 1_000);
        assert_eq!(e.bid[i], 1.0999);
        // USDCAD : premier tick à 5 000 -> toujours rien
        let j = e.sidx["USDCAD"];
        assert_eq!(e.last_t[j], NO_TICK);
    }

    /// T4 : rembobinage — sync(t2) puis sync(t1<t2) ne fige rien.
    #[test]
    fn t4_rewind_reexposes_past() {
        let mut e = Engine::new(vec![series(
            "EURUSD",
            &[(1_000, 1.0999, 1.1001), (9_000, 1.2000, 1.2002)],
        )]);
        e.sync(9_500);
        let i = e.sidx["EURUSD"];
        assert_eq!(e.last_t[i], 9_000);
        e.sync(1_500);
        assert_eq!(e.last_t[i], 1_000);
        assert_eq!(e.bid[i], 1.0999);
    }

    /// Fraîcheur bilatérale : quote future marquée -> rejetée (T3),
    /// quote trop vieille -> rejetée.
    #[test]
    fn t3_bilateral_freshness() {
        let mut e = Engine::new(vec![series("EURUSD", &[(1_000, 1.0, 1.001)])]);
        e.sync(1_000);
        let i = e.sidx["EURUSD"];
        assert!(e.ok(i, 1_200, 500.0));
        assert!(!e.ok(i, 2_000, 500.0)); // trop vieille
        e.last_t[i] = 2_000; // injection : quote "du futur" à t=1500
        assert!(!e.ok(i, 1_500, 5_000.0));
    }

    /// quote_at : dichotomie pure, frontières incluses/exclues.
    #[test]
    fn quote_at_boundaries() {
        let e = Engine::new(vec![series(
            "EURUSD",
            &[
                (1_000, 1.0, 1.001),
                (2_000, 1.1, 1.101),
                (3_000, 1.2, 1.201),
            ],
        )]);
        let i = e.sidx["EURUSD"];
        assert!(e.quote_at(i, 999).is_none());
        assert_eq!(e.quote_at(i, 1_000).unwrap().0, 1_000); // inclusif
        assert_eq!(e.quote_at(i, 1_999).unwrap().0, 1_000);
        assert_eq!(e.quote_at(i, 3_500).unwrap().0, 3_000);
    }

    /// Ordre des événements à timestamp égal : tri stable = ordre de la
    /// table FX (comme np.argsort stable sur concat par paire en ordre FX).
    /// C'est l'ordre que NautilusTrader doit reproduire (ts_init + ordre
    /// d'ajout des instruments dans l'ordre FX) — sinon jitter de parité.
    #[test]
    fn ev_order_at_equal_timestamps_follows_fx_table() {
        // NZDCHF = arête 27, EURUSD = arête 0, CADJPY = arête 22
        // -> à ts égal : EURUSD, puis CADJPY, puis NZDCHF (PAS alphabétique)
        let mut e = Engine::new(vec![
            series("NZDCHF", &[(1_000, 0.5, 0.501), (1_000, 0.5, 0.501)]),
            series("EURUSD", &[(1_000, 1.1, 1.101), (1_000, 1.1, 1.101)]),
            series("CADJPY", &[(1_000, 110.0, 110.1), (1_000, 110.0, 110.1)]),
        ]);
        let at_1000: Vec<usize> =
            e.ev.iter()
                .filter(|(t, _)| *t == 1_000)
                .map(|(_, si)| *si)
                .collect();
        let expected = [
            e.sidx["EURUSD"],
            e.sidx["EURUSD"],
            e.sidx["CADJPY"],
            e.sidx["CADJPY"],
            e.sidx["NZDCHF"],
            e.sidx["NZDCHF"],
        ];
        assert_eq!(at_1000, expected);
        // et le parcours tick() ne saute jamais dans le futur
        e.sync(1_000);
        assert!(e.last_t.iter().all(|&lt| lt <= 1_000));
    }

    /// Audit live : une quote croisée (ask < bid) ou non finie ne doit
    /// NI publier un état exploitable (spread négatif = edge fantôme
    /// qui passerait fx_ok) NI corrompre la médiane incrémentale.
    /// Le client filtre déjà en amont — défense en profondeur du moteur.
    #[test]
    fn ingest_rejects_crossed_or_nonfinite_quotes() {
        let mut e = Engine::live();
        let si = e.sidx["EURUSD"];
        e.ingest(
            si,
            Quote {
                ts_ms: 1_000,
                bid: 1.10,
                ask: 1.102,
                vol: 1.0,
            },
        );
        // croisée : ask < bid — rejetée (ni publiée ni dans la médiane)
        e.ingest(
            si,
            Quote {
                ts_ms: 2_000,
                bid: 1.105,
                ask: 1.104,
                vol: 1.0,
            },
        );
        // NaN — rejetée pareil
        e.ingest(
            si,
            Quote {
                ts_ms: 3_000,
                bid: f64::NAN,
                ask: 1.20,
                vol: 1.0,
            },
        );
        assert_eq!(e.last_t[si], 1_000, "quote invalide publiée");
        assert!((e.med[si] - 0.002).abs() < 1e-12, "médiane corrompue");
    }

    /// ingest : publie sans curseur, médiane incrémentale = médiane exacte,
    /// ts égal remplace la quote sans re-pousser le spread.
    #[test]
    fn ingest_live_updates_state_and_median() {
        let mut e = Engine::live();
        let si = e.sidx["EURUSD"];
        e.ingest(
            si,
            Quote {
                ts_ms: 1_000,
                bid: 1.10,
                ask: 1.102,
                vol: 1.0,
            },
        );
        assert_eq!(e.last_t[si], 1_000);
        assert!((e.med[si] - 0.002).abs() < 1e-12);
        // spreads 0.002, 0.004, 0.003 -> médiane 0.003
        for (t, a) in [(2_000i64, 1.104f64), (3_000, 1.103)] {
            e.ingest(
                si,
                Quote {
                    ts_ms: t,
                    bid: 1.10,
                    ask: a,
                    vol: 1.0,
                },
            );
        }
        assert!((e.med[si] - 0.003).abs() < 1e-12);
        // ts égal : la quote est remplacée MAIS le spread du tick reçu
        // compte dans la médiane (miroir du préchargé où les deux entrées
        // existent) -> spreads [0.002, 0.004, 0.003, 0.0] -> 0.0025
        e.ingest(
            si,
            Quote {
                ts_ms: 3_000,
                bid: 1.10,
                ask: 1.10,
                vol: 1.0,
            },
        );
        assert_eq!(e.last_t[si], 3_000);
        assert_eq!(e.ask[si], 1.10);
        assert_eq!(e.data[si].len(), 3);
        assert!((e.med[si] - 0.0025).abs() < 1e-12);
    }

    /// Audit OG2-D1 : un spread NaN/infini ne doit JAMAIS entrer dans la
    /// médiane — OrdF64 traiterait NaN comme égal à tout : il finit dans le
    /// tas `hi` et déplace les vraies valeurs vers `lo` (médiane silencieuse
    /// fausse mais finie -> spread_cap incohérent).
    #[test]
    fn spread_median_ignores_non_finite() {
        let mut m = SpreadMedian::default();
        for x in [0.001, 0.003, f64::NAN, f64::NAN, 0.002] {
            m.push(x);
        }
        // sans filtre : les NaN occupent `hi`, 0.003 est repoussé en tête de
        // `lo` -> médiane 0.003 au lieu de 0.002
        assert!((m.median() - 0.002).abs() < 1e-15);
        assert!(m.median().is_finite());
    }

    /// usd_price : mid direct (EURUSD) et inverse (USDJPY), ok mask respecté.
    #[test]
    fn usd_price_direct_inverse() {
        let mut e = Engine::new(vec![
            series("EURUSD", &[(1_000, 1.10, 1.102)]),
            series("USDJPY", &[(1_000, 149.9, 150.1)]),
        ]);
        e.sync(1_500);
        let ok = vec![true; e.data.len()];
        let up = e.usd_price(&ok);
        assert_eq!(up[0], 1.0); // USD
        assert!((up[1] - 1.101).abs() < 1e-12); // EUR : mid direct
        assert!((up[3] - 1.0 / 150.0).abs() < 1e-12); // JPY : 1/mid inverse
                                                      // symbole non-ok -> devise absente -> up NaN
        let mut ok2 = vec![true; e.data.len()];
        ok2[e.sidx["EURUSD"]] = false;
        assert!(e.usd_price(&ok2)[1].is_nan());
    }
}
