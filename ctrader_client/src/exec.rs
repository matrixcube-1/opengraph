//! Machine d'état d'exécution séquentielle — reprise des transitions de
//! `nt_realism.py` adaptées à la venue réelle.
//!
//! ```text
//! leg li : intent -> [sent] -> resolution
//!   Unfilled            -> abort(reason) + unwind des deltas
//!   Filled frac < q_min -> abort("q<q_min") + unwind
//!   Filled frac >= q_min -> fills.push((frac,px_node)) ; acc *= frac*px ; li+1
//! li == n_legs -> Done + exec_settle (modèle) ; positions restent ouvertes
//!   (résiduel intentionnel Convention A — flatten fin de session).
//! ```

use std::collections::BTreeMap;

use opengraph::engine::Engine;
use opengraph::paths::PathTables;

use crate::orders::{leg_intent, LegIntent, LegResolution};
use crate::symbols::{SymInfo, SymTable};

/// Fraction minimum de fill (P figé).
pub const Q_MIN: f64 = 0.5;

/// Spécification d'une jambe du chemin `pi` (pré-calculée au fire).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LegSpec {
    pub edge: u8,
    pub d: i8,
    pub node_from: u8,
    pub node_to: u8,
}

/// Déroule un chemin `pi` en spécifications de jambes (`_legs_of` du POC).
pub fn legs_of(pi: usize, pt: &PathTables) -> Vec<LegSpec> {
    let mut node = pt.p_s[pi];
    let mut out = Vec::with_capacity(pt.p_l[pi] as usize);
    for li in 0..pt.p_l[pi] as usize {
        let e = pt.p_e[pi][li] as u8;
        let d = pt.p_dir[pi][li];
        let (a, b) = Engine::edge_nodes(e);
        let nt = if d == 1 { b } else { a };
        out.push(LegSpec {
            edge: e,
            d,
            node_from: node,
            node_to: nt,
        });
        node = nt;
    }
    out
}

/// Issue d'une résolution de jambe.
#[derive(Debug, Clone, PartialEq)]
pub enum LegOutcome {
    /// Jambe remplie, enchaîner sur la suivante (`intent_for`).
    Advance { frac_eff: f64, px_node: f64 },
    /// Séquence terminée : fills complets -> exec_settle par l'appelant.
    Done,
    /// Abandon : unwind des `pos_deltas` requis.
    Abort { reason: String },
}

/// Séquence en cours — une seule à la fois (parité `t_free` du POC).
pub struct PathExec {
    pub pi: usize,
    pub t0: i64,
    pub legs: Vec<LegSpec>,
    /// Index de la jambe en cours (= nb de jambes résolues).
    pub li: usize,
    /// Quantité de devise nœud courante (post-fills).
    pub acc: f64,
    /// `(frac_eff, px_node)` par jambe remplie — entrée d'`exec_settle`.
    pub fills: Vec<(f64, f64)>,
    /// Deltas de position créés par la séquence : edge -> base units
    /// signés (`+` = long base). Source de vérité pour l'unwind.
    pub pos_deltas: BTreeMap<u8, f64>,
    /// `clientOrderId` de la jambe en vol (None entre les envois).
    pub in_flight: Option<LegIntent>,
}

impl PathExec {
    /// Démarre une séquence : `acc` = `start_qty(t0, pi)` (même marque
    /// que le moteur — Convention A).
    pub fn new(t0: i64, pi: usize, acc: f64, pt: &PathTables) -> Self {
        Self {
            pi,
            t0,
            legs: legs_of(pi, pt),
            li: 0,
            acc,
            fills: Vec::new(),
            pos_deltas: BTreeMap::new(),
            in_flight: None,
        }
    }

    /// Jambe courante à exécuter (None si toutes résolues).
    pub fn current_leg(&self) -> Option<LegSpec> {
        self.legs.get(self.li).copied()
    }

    /// Intention d'envoi de la jambe courante à la quote `(bid, ask)`.
    pub fn intent_for(
        &mut self,
        bid: f64,
        ask: f64,
        si: &SymInfo,
        coid: String,
    ) -> Option<LegIntent> {
        let leg = self.current_leg()?;
        let i = leg_intent(
            leg.edge,
            leg.d,
            leg.node_from,
            leg.node_to,
            self.acc,
            bid,
            ask,
            si,
            coid,
        )?;
        self.in_flight = Some(i.clone());
        Some(i)
    }

    /// Résolution terminale de la jambe en vol.
    /// `px_fill` = VWAP venue, `qty_base` = volume rempli (unités de base).
    pub fn apply_resolution(&mut self, res: &LegResolution) -> LegOutcome {
        let leg = match self.current_leg() {
            Some(l) => l,
            None => return LegOutcome::Done,
        };
        // `oq` = quantité envoyée de la jambe (après arrondi au pas),
        // comme `oq` de `_resolve_leg` dans le POC — pas l'`acc` brut.
        let oq = self
            .in_flight
            .as_ref()
            .map(|i| i.qty_base)
            .unwrap_or(self.acc);
        self.in_flight = None;
        let (qty_filled, px_fill) = match res {
            LegResolution::Unfilled { reason } => {
                return LegOutcome::Abort {
                    reason: reason.clone(),
                };
            }
            LegResolution::Filled { qty_base, px_fill, .. } => (*qty_base, *px_fill),
        };
        // position venue créée par le fill (signe : + long base)
        let delta = if leg.d == 1 { -qty_filled } else { qty_filled };
        *self.pos_deltas.entry(leg.edge).or_insert(0.0) += delta;

        // miroir _resolve_leg : frac_eff sur la quantité prévue de la jambe
        let frac_eff = if leg.d == 1 {
            qty_filled / oq
        } else {
            qty_filled * px_fill / self.acc
        };
        if frac_eff < Q_MIN {
            // le fill partiel reste une position réelle -> unwind
            return LegOutcome::Abort {
                reason: "q<q_min".into(),
            };
        }
        let px_node = if leg.d == 1 { px_fill } else { 1.0 / px_fill };
        self.fills.push((frac_eff.min(1.0), px_node));
        self.acc = self.acc * frac_eff * px_node;
        self.li += 1;
        if self.li >= self.legs.len() {
            LegOutcome::Done
        } else {
            LegOutcome::Advance { frac_eff, px_node }
        }
    }

    /// Plan d'unwind : pour chaque edge non nul, l'ordre IOC inverse.
    /// `+delta` = long base -> SELL ; `-delta` = short base -> BUY.
    pub fn unwind_plan(&self) -> Vec<(u8, ProtoTrade, f64)> {
        self.pos_deltas
            .iter()
            .filter(|(_, &d)| d.abs() > 1e-9)
            .map(|(&e, &d)| {
                if d > 0.0 {
                    (e, ProtoTrade::Sell, d)
                } else {
                    (e, ProtoTrade::Buy, -d)
                }
            })
            .collect()
    }

    /// Marque à jour les deltas quand un unwind fill complète.
    pub fn apply_unwind_fill(&mut self, edge: u8, trade: ProtoTrade, qty_base: f64) {
        let d = self.pos_deltas.entry(edge).or_insert(0.0);
        match trade {
            ProtoTrade::Sell => *d -= qty_base,
            ProtoTrade::Buy => *d += qty_base,
        }
    }

    /// Résiduel réel restant (devise nœud courante) après la séquence —
    /// équivalent `_resid_pieces` du POC, calculé sur les deltas réels.
    /// Pour `Done` : `acc` en `legs.last().node_to`.
    pub fn residual_ccy(&self) -> (u8, f64) {
        let node = self
            .legs
            .last()
            .map(|l| l.node_to)
            .unwrap_or_else(|| 0);
        (node, self.acc)
    }
}

/// Sens d'un ordre de débouclage (unwind).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProtoTrade {
    Buy,
    Sell,
}

/// Bouclage modèle après `Done` : `engine.exec_settle` sur les fills.
pub fn settle(
    engine: &mut Engine,
    t0: i64,
    pi: usize,
    fills: &[(f64, f64)],
    aborted: bool,
    pt: &PathTables,
) -> Option<(f64, u8, u8, f64)> {
    engine.exec_settle(t0, pi, fills, aborted, pt)
}

/// Garde-fou : le symbole d'un plan d'unwind doit exister dans la table.
pub fn unwind_intent(
    edge: u8,
    trade: ProtoTrade,
    qty_base: f64,
    bid: f64,
    ask: f64,
    st: &SymTable,
    coid: String,
) -> Option<LegIntent> {
    let si = st.by_edge(edge)?;
    // unwind = liquidation : SELL frappe le bid (limite bid-tol),
    // BUY frappe l'ask (limite ask+tol) — même bande que les jambes.
    let d = if trade == ProtoTrade::Sell { 1 } else { -1 };
    // acc factice : qty_base est déjà en unités de base -> pour d=+1
    // leg_intent attend `acc` en devise nœud (ici = base) => acc = qty_base
    // pour d=-1 : acc = qty_base * ask (quote à dépenser) -> qty/ask = qty_base
    let acc = if trade == ProtoTrade::Sell {
        qty_base
    } else {
        qty_base * ask
    };
    leg_intent(edge, d, 0, 0, acc, bid, ask, si, coid)
}

#[cfg(test)]
mod tests {
    use super::*;
    use opengraph::engine::{Engine, SymbolSeries};
    use opengraph::paths::build_tables;
    use opengraph::universe::{ci, FX};

    fn sym_all() -> SymTable {
        let mut info: [Option<SymInfo>; 28] = std::array::from_fn(|_| None);
        for e in 0..28 {
            info[e] = Some(SymInfo {
                symbol_id: e as i64 + 1,
                digits: if FX[e].ends_with("JPY") { 3 } else { 5 },
                min_volume: 1000,
                step_volume: 100,
                enabled: true,
            });
        }
        SymTable { info }
    }

    /// Chemin JPY -> CAD via CADJPY (1 jambe, d=-1 : achat CADJPY).
    fn path_jpy_cad(pt: &PathTables) -> usize {
        let e = FX.iter().position(|&s| s == "CADJPY").unwrap() as u8;
        (0..pt.len())
            .find(|&pi| {
                pt.paths[pi].len() == 1 && pt.paths[pi][0] == e
                    && pt.p_s[pi] == ci("JPY")
            })
            .unwrap()
    }

    #[test]
    fn legs_of_propagates_nodes() {
        let pt = build_tables();
        // chemin 3 jambes : nœuds chaînés nf->nt->nt2->nt3
        let pi = (0..pt.len()).find(|&pi| pt.p_l[pi] == 3).unwrap();
        let legs = legs_of(pi, &pt);
        assert_eq!(legs.len(), 3);
        assert_eq!(legs[0].node_from, pt.p_s[pi]);
        assert_eq!(legs[0].node_to, legs[1].node_from);
        assert_eq!(legs[1].node_to, legs[2].node_from);
        assert_eq!(legs[2].node_to, pt.p_end[pi]);
        for l in legs.iter() {
            let (a, b) = Engine::edge_nodes(l.edge);
            assert_eq!(l.node_from, if l.d == 1 { a } else { b });
            assert_eq!(l.node_to, if l.d == 1 { b } else { a });
        }
    }

    #[test]
    fn seq_full_fill_advances_acc() {
        let pt = build_tables();
        let st = sym_all();
        let pi = path_jpy_cad(&pt);
        // acc = 15M JPY ; fill BUY CADJPY 90_909 CAD @ 165.02
        let mut seq = PathExec::new(1_000, pi, 15_000_000.0, &pt);
        let leg = seq.current_leg().unwrap();
        assert_eq!(leg.d, -1);
        let si = st.by_edge(leg.edge).unwrap();
        let coid = "t1".to_string();
        let i = seq
            .intent_for(165.00, 165.02, si, coid.clone())
            .expect("intent");
        let qty = i.qty_base; // ~90_909 CAD
        let res = LegResolution::Filled {
            qty_base: qty,
            px_fill: 165.02,
            order_id: 1,
        };
        match seq.apply_resolution(&res) {
            LegOutcome::Done => {
                assert_eq!(seq.fills.len(), 1);
                let (frac, px_node) = seq.fills[0];
                assert!((frac - 1.0).abs() < 0.02, "frac ~1 : {frac}");
                assert!((px_node - 1.0 / 165.02).abs() < 1e-9);
                // acc = JPY * (1/165.02) = ~90_894 CAD
                assert!((seq.acc - 15_000_000.0 / 165.02).abs() / seq.acc < 0.02);
                // position : +qty CADJPY long
                assert!((seq.pos_deltas[&leg.edge] - qty).abs() < 1e-9);
            }
            other => panic!("attendu Done, eu {other:?}"),
        }
    }

    #[test]
    fn seq_unfilled_aborts() {
        let pt = build_tables();
        let st = sym_all();
        let pi = path_jpy_cad(&pt);
        let mut seq = PathExec::new(1_000, pi, 15_000_000.0, &pt);
        let leg = seq.current_leg().unwrap();
        let si = st.by_edge(leg.edge).unwrap();
        seq.intent_for(165.0, 165.02, si, "t".into());
        let r = seq.apply_resolution(&LegResolution::Unfilled {
            reason: "cancelled_no_fill".into(),
        });
        assert_eq!(r, LegOutcome::Abort { reason: "cancelled_no_fill".into() });
        assert!(seq.pos_deltas.is_empty(), "pas de fill -> pas de position");
    }

    #[test]
    fn seq_partial_below_qmin_aborts_with_position() {
        let pt = build_tables();
        let st = sym_all();
        let pi = path_jpy_cad(&pt);
        let mut seq = PathExec::new(1_000, pi, 15_000_000.0, &pt);
        let leg = seq.current_leg().unwrap();
        let si = st.by_edge(leg.edge).unwrap();
        let i = seq.intent_for(165.0, 165.02, si, "t".into()).unwrap();
        // fill 20 % de l'acc -> frac_eff ~0.2 < 0.5 -> abort q<q_min
        let small = i.qty_base * 0.2;
        let r = seq.apply_resolution(&LegResolution::Filled {
            qty_base: small,
            px_fill: 165.02,
            order_id: 1,
        });
        assert_eq!(r, LegOutcome::Abort { reason: "q<q_min".into() });
        // le partiel est une VRAIE position -> unwind plan SELL small
        let plan = seq.unwind_plan();
        assert_eq!(plan.len(), 1);
        let (e, tr, q) = plan[0];
        assert_eq!(e, leg.edge);
        assert_eq!(tr, ProtoTrade::Sell);
        assert!((q - small).abs() < 1e-9);
    }

    #[test]
    fn unwind_plan_both_sides() {
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let mut seq = PathExec::new(1_000, pi, 15_000_000.0, &pt);
        let e = seq.legs[0].edge;
        seq.pos_deltas.insert(e, 500.0);
        seq.pos_deltas.insert(5, -300.0); // short base sur arête 5
        let plan = seq.unwind_plan();
        assert_eq!(plan.len(), 2);
        // BTreeMap: arête 5 d'abord
        assert_eq!(plan[0], (5u8, ProtoTrade::Buy, 300.0));
        assert_eq!(plan[1], (e, ProtoTrade::Sell, 500.0));
    }

    #[test]
    fn unwind_intent_uses_ioc_band() {
        let st = sym_all();
        // clôturer 500 unités long CADJPY -> SELL, limite bid-tol
        let e = FX.iter().position(|&s| s == "CADJPY").unwrap() as u8;
        let i = unwind_intent(e, ProtoTrade::Sell, 500.0, 165.0, 165.02, &st, "u".into())
            .unwrap();
        let tol = 3.0 * 0.8e-4;
        assert!((i.limit_px - 165.0 * (1.0 - tol)).abs() < 1e-9);
        assert_eq!(i.volume, 500 * 100); // centièmes d'unité
        // BUY : acc factice = qty*ask -> qty_base = qty
        let j = unwind_intent(e, ProtoTrade::Buy, 300.0, 165.0, 165.02, &st, "u".into())
            .unwrap();
        assert!((j.limit_px - 165.02 * (1.0 + tol)).abs() < 1e-9);
        assert!((j.qty_base - 300.0).abs() < 1.0);
    }
}
