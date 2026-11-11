//! Exécution séquentielle E1-E9 du PANIER MARGE COMPLET (bug 11/11 :
//! l'exécuteur ne tradait que `path[]` alors que l'évaluateur pricait
//! l'entrée USD→s0 et la sortie end→USD — prouvé live : 7 séquences
//! legs=1, −13,63 $ sur cTrader).
//!
//! Le panier = ordres `[USD→s0] + jambes + [end→USD]` — chaque ordre est
//! une position marge réelle ; le résiduel terminal est USD (pas de
//! marquage `usd_liq` fantôme). Chaque ordre, en séquence :
//!
//! ```text
//! t_fill  = t_prev + lat_ms
//! q_send  = quote_at(sym, t_prev)    (quote de décision)
//! q_now   = quote_at(sym, t_fill)    (VRAI tick <= t_fill, sans fraîcheur)
//!
//! fill absent ou q_send invalide ..................... ABORT "no quote"
//! move = |q_now/q_send - 1|
//!   move > 3.slip .................................... ABORT "kill IOC"
//!   slip < move <= 3.slip ............................ frac = 0.6 (partiel)
//! garde volume : frac = min(frac, vol_base/need_base)  [spécifié Rust]
//!   frac < q_min ..................................... ABORT "q<q_min"
//! partiel : cash += qty(1-frac) x liq[node] (marquage exécutable)
//! qty *= frac x q_now ; comm += comm_per_side ; node avance
//! ```
//!
//! Fin : `pnl = qty + cash - NOTIONAL - 2xcomm - unwind` (qty déjà en USD,
//! unwind = re-traversée du spread par position remplie, entrée/sortie
//! incluses). Abort : `pnl = -abort_cost + cash - comm` où abort_cost =
//! somme par position remplie de `med_rel x notional + comm` (débouclage
//! IOC croisant le spread médian — audit OG2-D2).
//!
//! ## Garde volume top-of-book (ajout spécifié vs POC)
//!
//! `vol` Dukas FX = millions de devise de base. L'ordre transite `need_base`
//! unités de base (qty si vente de base, qty x q_now si achat de base) ;
//! si le volume affiché au tick de fill n'y suffit pas, `frac` est plafonné.
//! `vol <= 0` ou non fini = aucune liquidité affichée -> frac 0 -> rejet.
//! Effet quasi nul à 100 K$ sur FX (~1-3 M affichés) mais testé.

use crate::engine::Engine;
use crate::fees::{ABORT_COST_PER_LEG_USD, NOTIONAL_USD};
use crate::paths::PathTables;
use crate::universe::usd_edge;

/// Paramètres d'une séquence d'exécution.
#[derive(Clone, Copy, Debug)]
pub struct ExecParams {
    /// Latence par jambe (ms, flottant — le POC utilise des lat non entières).
    pub lat_ms: f64,
    /// Mouvement de prix toléré entre envoi et fill, en bp.
    pub tau_slip_bp: f64,
    /// Fraction minimale de fill acceptée (rejet en dessous).
    pub q_min: f64,
    /// Garde volume top-of-book — `false` : parité stricte POC ;
    /// `true` : spécification Rust (production).
    pub vol_guard: bool,
}

/// Issue d'une séquence d'exécution.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum ExecOutcome {
    /// Séquence terminée.
    Done {
        pnl_usd: f64,
        filled_legs: u8,
        partial_legs: u8,
        dur_ms: f64,
    },
    /// Abandonnée en cours de route.
    Abort {
        reason: AbortReason,
        pnl_usd: f64,
        filled_legs: u8,
        partial_legs: u8,
        dur_ms: f64,
    },
}

impl ExecOutcome {
    pub fn pnl(&self) -> f64 {
        match *self {
            ExecOutcome::Done { pnl_usd, .. } | ExecOutcome::Abort { pnl_usd, .. } => pnl_usd,
        }
    }

    pub fn dur_ms(&self) -> f64 {
        match *self {
            ExecOutcome::Done { dur_ms, .. } | ExecOutcome::Abort { dur_ms, .. } => dur_ms,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AbortReason {
    NoQuote,
    KillIoc,
    BelowQMin,
}

impl Engine {
    /// Rejoue la séquence IOC du chemin `pi` détecté à `t0`.
    /// Retourne None si une donnée requise est absente (POC : skip).
    pub fn exec_seq(
        &mut self,
        t0: i64,
        pi: usize,
        ep: &ExecParams,
        pt: &PathTables,
    ) -> Option<ExecOutcome> {
        let orders = self.orders_of(pi, pt)?;
        self.sync(t0);
        // usd_price / usd_liq calculés sur TOUS les mids courants (ok = ones)
        // — le marquage du reliquat de partiel ne subit pas la garde de fraîcheur.
        let ok_all = vec![true; self.data.len()];
        let up = self.usd_price(&ok_all);
        let up_liq = self.usd_liq_price(&ok_all);
        let comm_liq = self.usd_liq_comm(&ok_all);
        let liq = |node: usize| -> (f64, f64) {
            let r = if up_liq[node].is_finite() {
                up_liq[node]
            } else {
                up[node]
            };
            let c = if comm_liq[node].is_finite() {
                comm_liq[node]
            } else if up[node].is_finite() && up[node] > 0.0 {
                self.comm_per_side / up[node]
            } else {
                self.comm_per_side
            };
            (r, c)
        };
        let order_edges: Vec<u8> = orders.iter().map(|&(e, _)| e).collect();
        let mut qty = NOTIONAL_USD; // on démarre en USD (entrée explicite)
        let mut cash = 0.0f64;
        let mut comm = 0.0f64;
        let mut t = t0 as f64;
        let mut filled = 0u8;
        let mut parts = 0u8;
        let slip = ep.tau_slip_bp * 1e-4;

        macro_rules! abort {
            ($why:expr) => {
                return Some(ExecOutcome::Abort {
                    reason: $why,
                    pnl_usd: -(self.abort_cost(&order_edges, filled)) + cash - comm,
                    filled_legs: filled,
                    partial_legs: parts,
                    dur_ms: t - t0 as f64,
                })
            };
        }

        for &(e, from) in &orders {
            let si = self.fx_si[e as usize].expect("filtré ci-dessus");
            let (a, b) = Self::edge_nodes(e);
            debug_assert!(
                from == a || from == b,
                "nœud {from} hors arête ({a},{b})"
            );
            let d: i8 = if from == a { 1 } else { -1 };
            let node = from as usize; // devise convertie par cet ordre
            let t_f = t + ep.lat_ms;

            let q_send = match self.quote_at_f(si, t) {
                Some((_, bid, ask, _, _)) => {
                    if d == 1 {
                        bid
                    } else {
                        1.0 / ask
                    }
                }
                None => f64::NAN,
            };
            let Some((_, bid_f, ask_f, avol_f, bvol_f)) = self.quote_at_f(si, t_f) else {
                abort!(AbortReason::NoQuote);
            };
            if !q_send.is_finite() || q_send <= 0.0 {
                abort!(AbortReason::NoQuote);
            }

            let q_now = if d == 1 { bid_f } else { 1.0 / ask_f };
            let mv = (q_now / q_send - 1.0).abs();
            if mv > 3.0 * slip {
                abort!(AbortReason::KillIoc);
            }
            let mut frac: f64 = if mv <= slip { 1.0 } else { 0.6 };

            // garde volume top-of-book — volume do LADO atingido
            // (sell lê bid_vol, buy lê ask_vol; antes lia max() dos dois)
            if ep.vol_guard {
                let vol_base = (if d == 1 { bvol_f } else { avol_f }) * 1e6;
                let need_base = if d == 1 { qty } else { qty * q_now };
                let frac_vol = if vol_base.is_finite() && vol_base > 0.0 && need_base > 0.0 {
                    (vol_base / need_base).min(1.0)
                } else {
                    0.0
                };
                frac = frac.min(frac_vol);
            }

            if frac < ep.q_min {
                abort!(AbortReason::BelowQMin);
            }
            if frac < 1.0 {
                let (r, c) = liq(node);
                cash += qty * (1.0 - frac) * r;
                comm += c;
                qty *= frac;
                parts += 1;
            }
            qty *= q_now;
            comm += self.comm_per_side;   // flat par position ~10K (parité eval)
            filled += 1;
            t = t_f;
        }

        // node == 0 : le panier termine en USD — usd_end = qty directement.
        // Unwind : chaque position remplie (entrée/jambes/sortie) se REFERME
        // à travers le book — traversée complète du spread + 1 comm
        // (audit marge 10/11 : mesuré live, settle -1,2 $ vs net détecté
        // -0,37 sans ce terme).
        let usd_end = qty;
        let mut unwind = 0.0f64;
        let mut comm_rt = comm;
        for &e in order_edges.iter().take(filled as usize) {
            let Some(si) = self.fx_si[e as usize] else {
                continue;
            };
            let mid = 0.5 * (self.bid[si] + self.ask[si]);
            unwind += if mid.is_finite() && mid > 0.0 {
                (self.ask[si] - self.bid[si]) / mid * NOTIONAL_USD
            } else {
                f64::NAN
            };
            comm_rt += self.comm_per_side;
        }
        Some(ExecOutcome::Done {
            pnl_usd: usd_end + cash - NOTIONAL_USD - comm_rt - unwind,
            filled_legs: filled,
            partial_legs: parts,
            dur_ms: t - t0 as f64,
        })
    }

    /// Ordres du panier marge du chemin `pi` : `(arête, nœud_depart)` —
    /// `[USD→s0] + jambes + [end→USD]` (bug panier 11/11). `None` si une
    /// arête requise n'est pas chargée dans l'engine.
    pub fn orders_of(&self, pi: usize, pt: &PathTables) -> Option<Vec<(u8, u8)>> {
        let start = pt.p_s[pi];
        let end = pt.p_end[pi];
        let mut orders: Vec<(u8, u8)> = Vec::with_capacity(pt.p_l[pi] as usize + 2);
        if start != 0 {
            let e = usd_edge(start)?;
            self.fx_si[e as usize]?;
            orders.push((e, 0));
        }
        let mut node = start;
        for li in 0..pt.p_l[pi] as usize {
            let e = pt.p_e[pi][li] as u8;
            self.fx_si[e as usize]?;
            orders.push((e, node));
            node = Self::edge_other(e, node);
        }
        if end != 0 {
            let e = usd_edge(end)?;
            self.fx_si[e as usize]?;
            orders.push((e, end));
        }
        debug_assert_eq!(node, end); // la dernière jambe termine bien à end
        Some(orders)
    }

    /// Montant de départ du panier — toujours `NOTIONAL_USD` en USD : le
    /// premier ordre est la conversion d'entrée USD→s0 (bug panier 11/11 :
    /// avant, la quantité était pré-convertie au mid dans la devise de
    /// départ et les conversions USD étaient pricées mais jamais tradées).
    /// `None` si une arête du panier n'est pas chargée.
    pub fn start_qty(&mut self, pi: usize, pt: &PathTables) -> Option<f64> {
        self.orders_of(pi, pt)?;
        Some(NOTIONAL_USD)
    }

    /// Coût de débouclage des positions remplies — audit OG2-D2 : le proxy
    /// fixe 12 $/position sous-estimait ~2× le coût réel sur les paires à
    /// spread large (NZDJPY ~2 bp → ~23 $). Pour chaque ordre rempli,
    /// l'unwind IOC croise le spread médian de l'arête et paie une
    /// commission : `med_rel x NOTIONAL + COMM`. Repli sur la constante
    /// quand le mid ou la médiane manquent (jambe métal du POC, warm-up).
    fn abort_cost(&self, legs: &[u8], filled: u8) -> f64 {
        legs.iter()
            .take(filled as usize)
            .map(|&e| {
                let Some(si) = self.fx_si[e as usize] else {
                    return ABORT_COST_PER_LEG_USD;
                };
                let mid = (self.bid[si] + self.ask[si]) * 0.5;
                let med = self.med[si];
                if med.is_finite() && mid.is_finite() && mid > 0.0 {
                    med / mid * NOTIONAL_USD + self.comm_per_side
                } else {
                    ABORT_COST_PER_LEG_USD
                }
            })
            .sum()
    }

    /// Boucle une séquence exécutée par une venue externe (ordres IOC
    /// réels NautilusTrader) — **même arithmétique de bouclage** que
    /// `exec_seq`, appliquée aux fills constatés plutôt qu'au modèle E1-E9.
    ///
    /// `fills` = `(frac_eff, px_node)` par **ordre du panier** complété
    /// (entrée USD→s0, jambes, sortie end→USD — `orders_of`), où :
    /// - `frac_eff` = fraction de la devise nœud effectivement convertie
    ///   (d=+1 : `filled_base/order_base` ; d=-1 : `filled_base*px_fill/acc`) ;
    /// - `px_node` = taux nœud→nœud (`px_fill` si d=+1, `1/px_fill` si d=-1).
    ///
    /// `aborted` : la séquence s'est arrêtée avant le dernier ordre
    /// (kill IOC / q<q_min / no quote) → `pnl = -abort_cost + cash - comm`,
    /// l'ordre avorté ne contribue rien (conservatisme POC, identique à
    /// `exec_seq` : ni comm ni produit de l'ordre fautif).
    ///
    /// Retourne `(pnl_usd, filled_legs, partial_legs, resid_usd)` où
    /// `resid_usd` = valeur de l'exposition restante marquée à la
    /// liquidation EXÉCUTABLE (Convention A stricte) sur l'état COURANT —
    /// ~0 pour un panier complet (termine en USD) ; jamais de `sync(t0)`
    /// en live (audit C3 : rembobiner publiait des prix périmés).
    /// En replay, appeler `sync(t0)` avant pour l'état de départ.
    /// None si une arête du panier n'est pas chargée.
    pub fn exec_settle(
        &mut self,
        pi: usize,
        fills: &[(f64, f64)],
        aborted: bool,
        pt: &PathTables,
    ) -> Option<(f64, u8, u8, f64)> {
        let orders = self.orders_of(pi, pt)?;
        // audit OG2-B5 : plus de fills que d'ordres -> orders[li] hors borne.
        if fills.len() > orders.len() {
            return None;
        }
        let ok_all = vec![true; self.data.len()];
        let up = self.usd_price(&ok_all);
        let up_liq = self.usd_liq_price(&ok_all);
        let comm_liq = self.usd_liq_comm(&ok_all);
        let liq = |node: usize| -> (f64, f64) {
            let r = if up_liq[node].is_finite() {
                up_liq[node]
            } else {
                up[node]
            };
            let c = if comm_liq[node].is_finite() {
                comm_liq[node]
            } else if up[node].is_finite() && up[node] > 0.0 {
                self.comm_per_side / up[node]
            } else {
                self.comm_per_side
            };
            (r, c)
        };
        let order_edges: Vec<u8> = orders.iter().map(|&(e, _)| e).collect();
        let mut node = 0usize; // USD — panier : départ en USD explicite
        let mut qty = NOTIONAL_USD;
        let mut cash = 0.0f64;
        let mut comm = 0.0f64;
        let mut filled = 0u8;
        let mut parts = 0u8;

        for (li, &(frac, px)) in fills.iter().enumerate() {
            let (_, from) = orders[li];
            node = from as usize;
            if frac < 1.0 {
                let (r, c) = liq(node);
                cash += qty * (1.0 - frac) * r;
                comm += c;
                qty *= frac;
                parts += 1;
            }
            qty *= px;
            comm += self.comm_per_side;   // flat par position (parité eval)
            filled += 1;
            node = Self::edge_other(order_edges[li], from) as usize;
        }

        // Panier complet : node = USD -> resid = qty ; abort partiel : la
        // devise courante est marquée à sa liquidation exécutable.
        let (r_end, _c_end) = liq(node);
        let resid_usd = if filled > 0 { qty * r_end } else { 0.0 };
        let pnl = if aborted {
            -(self.abort_cost(&order_edges, filled)) + cash - comm
        } else {
            // même unwind marge que exec_seq : clôtures réelles par
            // position (traversée + comm), entrée/sortie incluses.
            let mut unwind = 0.0f64;
            for &e in order_edges.iter().take(filled as usize) {
                let Some(si) = self.fx_si[e as usize] else {
                    continue;
                };
                let mid = 0.5 * (self.bid[si] + self.ask[si]);
                unwind += if mid.is_finite() && mid > 0.0 {
                    (self.ask[si] - self.bid[si]) / mid * NOTIONAL_USD
                } else {
                    f64::NAN
                };
                comm += self.comm_per_side;
            }
            resid_usd + cash - NOTIONAL_USD - comm - unwind
        };
        Some((pnl, filled, parts, resid_usd))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::SymbolSeries;
    use crate::paths::build_tables;
    use crate::universe::{ci, FX};

    const EP: ExecParams = ExecParams {
        lat_ms: 2.5,
        tau_slip_bp: 0.8,
        q_min: 0.5,
        vol_guard: true,
    };

    /// Livre avec CADJPY distordu ; ticks répétés pour simuler le passage
    /// du temps entre envoi (t) et fill (t + lat).
    fn book_distorted() -> Vec<SymbolSeries> {
        let ups: [f64; 8] = [
            1.0,
            1.10,
            1.30,
            1.0 / 150.0,
            1.0 / 0.90,
            1.0 / 1.35,
            0.65,
            0.60,
        ];
        FX.iter()
            .map(|&s| {
                let a = ci(&s[0..3]) as usize;
                let b = ci(&s[3..6]) as usize;
                let mut mid = ups[a] / ups[b];
                if s == "CADJPY" {
                    mid *= 0.995; // distorsion -0,5 %
                }
                // même quote à t=1000 et t=1002 : le fill à t+2,5ms
                // voit le tick 1002 (<= 1002.5) — le mouvement est capté
                SymbolSeries {
                    sym: s,
                    ts: vec![1_000, 1_002],
                    bid: vec![mid * 0.9999; 2],
                    ask: vec![mid * 1.0001; 2],
                    avol: vec![1.0; 2],
                    bvol: vec![1.0; 2],
                }
            })
            .collect()
    }

    fn path_jpy_cad(pt: &PathTables) -> usize {
        let e_idx = FX.iter().position(|&s| s == "CADJPY").unwrap() as u8;
        (0..pt.len())
            .find(|&pi| {
                pt.paths[pi].len() == 1
                    && pt.paths[pi][0] == e_idx
                    && pt.p_dir[pi][0] == -1
                    && pt.p_s[pi] == ci("JPY")
            })
            .unwrap()
    }

    /// Fill complet à la quote de fill : P&L recalculé à la main.
    /// Panier JPY->CAD : [USDJPY (USD->JPY @bid) -> CADJPY (JPY->CAD
    /// @1/ask) -> USDCAD (CAD->USD @1/ask)] = 3 positions réelles.
    #[test]
    fn exec_full_fill_matches_hand_computation() {
        let mut e = Engine::new(book_distorted());
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let ups: [f64; 8] = [
            1.0,
            1.10,
            1.30,
            1.0 / 150.0,
            1.0 / 0.90,
            1.0 / 1.35,
            0.65,
            0.60,
        ];
        let r = e.exec_seq(1_000, pi, &EP, &pt).expect("exec");
        let ExecOutcome::Done {
            pnl_usd,
            dur_ms,
            filled_legs,
            partial_legs,
        } = r
        else {
            panic!("attendu Done");
        };
        let bid_uj = (ups[0] / ups[3]) * 0.9999;   // USDJPY mid 150
        let ask_cj = (ups[5] / ups[3] * 0.995) * 1.0001; // CADJPY distordu
        let ask_uc = (ups[0] / ups[5]) * 1.0001;   // USDCAD mid 1.35
        let usd_end = crate::fees::NOTIONAL_USD * bid_uj / ask_cj / ask_uc;
        // comm : 3 positions x aller-retour ; unwind : 3 x spread rel 2e-4.
        let expected = usd_end - crate::fees::NOTIONAL_USD
            - 6.0 * crate::fees::comm_usd()
            - 3.0 * 2e-4 * crate::fees::NOTIONAL_USD;
        assert!((pnl_usd - expected).abs() < 1e-6);
        assert_eq!(dur_ms, 7.5); // 3 ordres x 2.5 ms
        assert_eq!(filled_legs, 3);
        assert_eq!(partial_legs, 0);
        assert!(pnl_usd > 0.0);
    }

    /// Prix adverse > 3.slip entre envoi et fill du 2e ordre -> kill IOC ;
    /// la 1re position (entrée USDJPY) est déjà remplie et se déboucle.
    #[test]
    fn exec_kill_ioc_on_adverse_move() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                // 3e tick à 1004 : send(1002.5) voit 1002 normal,
                // fill(1005) voit 1004 haussé -> move >> 3x0.8bp
                s.ts.push(1_004);
                s.bid.push(s.bid[1]);
                s.ask.push(s.ask[1] * 1.01);
                s.avol.push(1.0);
                s.bvol.push(1.0);
            }
        }
        let mut e = Engine::new(series);
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let r = e.exec_seq(1_000, pi, &EP, &pt).unwrap();
        let ExecOutcome::Abort {
            reason,
            pnl_usd,
            filled_legs,
            ..
        } = r
        else {
            panic!("attendu Abort");
        };
        assert_eq!(reason, AbortReason::KillIoc);
        assert_eq!(filled_legs, 1); // entrée USDJPY remplie
        // pnl = -abort_cost(USDJPY) - comm ouverture = -(2,0 + 0,4) - 0,4
        assert!((pnl_usd + 2.8).abs() < 0.05);
    }

    /// Pas de tick <= t_fill du 2e ordre -> abort "no quote" après
    /// l'entrée USDJPY remplie (débouclage + comm facturés).
    #[test]
    fn exec_no_quote_aborts() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                s.ts = vec![1_006, 1_007]; // plus de tick <= 1005
            }
        }
        let mut e = Engine::new(series);
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let r = e.exec_seq(1_000, pi, &EP, &pt).unwrap();
        let ExecOutcome::Abort {
            reason,
            pnl_usd,
            filled_legs,
            partial_legs,
            dur_ms,
        } = r
        else {
            panic!("attendu Abort");
        };
        assert_eq!(reason, AbortReason::NoQuote);
        assert_eq!((filled_legs, partial_legs), (1, 0));
        assert_eq!(dur_ms, 2.5); // t n'avance qu'au fill : abort à 1002.5
        assert!((pnl_usd + 2.8).abs() < 0.05);
    }

    /// Garde volume : tick de fill avec volume insuffisant -> rejet q<q_min.
    #[test]
    fn exec_volume_guard_rejects_thin_book() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                s.avol[1] = 0.005; // 5 K CAD no ask < ~7,4 K requis a 10 K (jambe buy)
            }
        }
        let mut e = Engine::new(series);
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let r = e.exec_seq(1_000, pi, &EP, &pt).unwrap();
        let ExecOutcome::Abort { reason, .. } = r else {
            panic!("attendu Abort (liquidite insuffisante)");
        };
        assert_eq!(reason, AbortReason::BelowQMin);
    }

    /// Garde volume lê o LADO atingido, não max(avol,bvol): livro com ask
    /// fino mas bid fundo deve abortar numa jambe buy — com max() passaria.
    #[test]
    fn exec_volume_guard_reads_hit_side_not_max() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                s.avol[1] = 0.005; // ask fino (lado que a compra atinge)
                s.bvol[1] = 50.0; // bid fundo — max() esconderia a falta
            }
        }
        let mut e = Engine::new(series);
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let r = e.exec_seq(1_000, pi, &EP, &pt).unwrap();
        let ExecOutcome::Abort { reason, .. } = r else {
            panic!("attendu Abort — ask_vol fino deve barrar mesmo com bid_vol fundo");
        };
        assert_eq!(reason, AbortReason::BelowQMin);
    }

    /// Mouvement intermédiaire entre send et fill du 2e ordre ->
    /// fill partiel 60 %, résiduel au cash ; panier complété (3 positions).
    #[test]
    fn exec_partial_fill_marks_residual() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                // tick 1004 haussé ~1.5 bp : send(1002.5) voit 1002,
                // fill(1005) voit 1004 -> frac 0.6 (slip=0.8bp)
                s.ts.push(1_004);
                s.bid.push(s.bid[1]);
                s.ask.push(s.ask[1] * (1.0 + 1.5e-4));
                s.avol.push(1.0);
                s.bvol.push(1.0);
            }
        }
        let mut e = Engine::new(series);
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let r = e.exec_seq(1_000, pi, &EP, &pt).unwrap();
        let ExecOutcome::Done {
            partial_legs,
            filled_legs,
            ..
        } = r
        else {
            panic!("attendu Done avec partiel");
        };
        assert_eq!(filled_legs, 3);
        assert_eq!(partial_legs, 1);
    }

    // --- exec_settle : bouclage sur fills externes (venue NT) ---

    /// Latence nulle pour les tests settle (fills à la quote de décision).
    const EP0: ExecParams = ExecParams {
        lat_ms: 0.0,
        tau_slip_bp: 0.8,
        q_min: 0.5,
        vol_guard: false,
    };

    /// Premier chemin à `n` jambes (déterministe — ordre de la table).
    fn path_n_legs(pt: &PathTables, n: u8) -> usize {
        (0..pt.len()).find(|&pi| pt.p_l[pi] == n).unwrap()
    }

    /// `exec_settle` sur les fills équivalents (frac=1, px=q_now) doit
    /// reproduire `exec_seq(lat=0)` à l'arrondi près — même chaîne qty,
    /// mêmes marquages cash/comm. Les fills couvrent TOUT le panier.
    #[test]
    fn settle_full_fills_matches_exec_seq_3_legs() {
        let mut e = Engine::new(book_distorted());
        let pt = build_tables();
        let pi = path_n_legs(&pt, 3);
        let orders = e.orders_of(pi, &pt).unwrap();
        let n_ord = orders.len() as u8;
        // fills externes = fill complet à la quote de t0 par ordre du panier
        let mut fills = Vec::new();
        for &(e_i, from) in &orders {
            let si = e.fx_si[e_i as usize].unwrap();
            let (a, _) = Engine::edge_nodes(e_i);
            let (_, bid, ask, _, _) = e.quote_at_f(si, 1_000.0).unwrap();
            fills.push((1.0, if from == a { bid } else { 1.0 / ask }));
        }
        e.sync(1_000); // replay : état à t0 (sync hors de exec_settle — C3)
        let (pnl_s, filled_s, parts_s, _resid_s) =
            e.exec_settle(pi, &fills, false, &pt).unwrap();
        let ExecOutcome::Done {
            pnl_usd,
            filled_legs,
            partial_legs,
            ..
        } = e.exec_seq(1_000, pi, &EP0, &pt).unwrap()
        else {
            panic!("attendu Done");
        };
        assert_eq!(filled_s, n_ord);
        assert_eq!(parts_s, 0);
        assert_eq!(filled_legs, n_ord);
        assert_eq!(partial_legs, 0);
        // même ordre d'opérations f64 -> égalité quasi bit à bit
        assert!((pnl_s - pnl_usd).abs() / pnl_usd.abs().max(1.0) < 1e-12);
    }

    /// Audit OG2-B5 : plus de fills que d'ordres du panier -> None, pas
    /// de panic index hors borne.
    #[test]
    fn settle_rejects_more_fills_than_legs() {
        let mut e = Engine::new(book_distorted());
        let pt = build_tables();
        let pi = path_jpy_cad(&pt); // chemin à 1 jambe -> panier de 3 ordres
        e.sync(1_000);
        assert!(e
            .exec_settle(
                pi,
                &[(1.0, 1.0), (1.0, 1.0), (1.0, 1.0), (1.0, 1.0)],
                false,
                &pt
            )
            .is_none());
    }

    /// Abort après positions remplies : pnl = -abort_cost + cash - comm
    /// avec abort_cost = med_rel x notional + comm par ordre rempli
    /// (audit OG2-D2) ; l'ordre avorté ne contribue rien.
    #[test]
    fn settle_abort_matches_hand_computation() {
        let mut e = Engine::new(book_distorted());
        let pt = build_tables();
        let pi = path_jpy_cad(&pt); // panier : USDJPY entrée + CADJPY + USDCAD
        e.sync(1_000);
        let (pnl, filled, parts, _resid) = e.exec_settle(pi, &[], true, &pt).unwrap();
        assert_eq!((pnl, filled, parts), (0.0, 0, 0)); // cout 0 x filled

        // 1er ordre (entrée USD->JPY via USDJPY, d=+1 -> px = bid) rempli
        // puis abort : pnl = -cout(USDJPY) + 0 - comm ouverture.
        let si_uj = e.fx_si[2].unwrap(); // USDJPY = arête 2
        let (_, bid_uj, ask_uj, _, _) = e.quote_at_f(si_uj, 1_000.0).unwrap();
        let (pnl2, filled2, _, resid2) = e
            .exec_settle(pi, &[(1.0, bid_uj)], true, &pt)
            .unwrap();
        assert_eq!(filled2, 1);
        // coût de débouclage par position : med_rel x notional + comm —
        // la fixture a un spread relatif de 2 bp -> 2,0 $ + 0,40 $ comm.
        let abort_cost = e.med[si_uj] / ((bid_uj + ask_uj) * 0.5)
            * crate::fees::NOTIONAL_USD
            + crate::fees::comm_usd();
        assert!((abort_cost - 2.4).abs() < 0.1);
        assert!((pnl2 - (-abort_cost - crate::fees::comm_usd())).abs() < 1e-9);
        // resid = qty JPY marquée à sa liquidation : 1/ask(USDJPY).
        assert!((resid2 - crate::fees::NOTIONAL_USD * bid_uj / ask_uj).abs() < 1e-6);
    }

    /// Partiel 60 % sur la 2e position via settle = même résiduel cash
    /// que exec_seq partiel.
    #[test]
    fn settle_partial_marks_residual_like_exec_seq() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                s.ts.push(1_004);
                s.bid.push(s.bid[1]);
                s.ask.push(s.ask[1] * (1.0 + 1.5e-4));
                s.avol.push(1.0);
                s.bvol.push(1.0);
            }
        }
        let mut e = Engine::new(series);
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let ep = ExecParams { lat_ms: 2.5, ..EP0 };
        let ExecOutcome::Done { pnl_usd, .. } = e.exec_seq(1_000, pi, &ep, &pt).unwrap() else {
            panic!("attendu Done");
        };
        // fills constatés par ordre : entrée pleine @bid, CADJPY 60 %
        // à la quote du tick 1004, sortie pleine @1/ask.
        let si_uj = e.fx_si[2].unwrap();
        let si_cj = e.fx_si[22].unwrap();
        let si_uc = e.fx_si[5].unwrap();
        let (_, bid_uj, _, _, _) = e.quote_at_f(si_uj, 1_000.0).unwrap();
        let (_, _, ask_f, _, _) = e.quote_at_f(si_cj, 1_004.0).unwrap();
        let (_, _, ask_uc, _, _) = e.quote_at_f(si_uc, 1_000.0).unwrap();
        e.sync(1_000);
        let (pnl_s, _, parts_s, residp) = e
            .exec_settle(
                pi,
                &[(1.0, bid_uj), (0.6, 1.0 / ask_f), (1.0, 1.0 / ask_uc)],
                false,
                &pt,
            )
            .unwrap();
        assert_eq!(parts_s, 1);
        assert!((pnl_s - pnl_usd).abs() / pnl_usd.abs().max(1.0) < 1e-12);
        // resid = qty USD final (node=USD -> marque 1, panier complet)
        assert!(residp > 0.0);
    }

    /// `start_qty` = NOTIONAL_USD — le panier démarre toujours en USD
    /// (l'entrée USD->s0 est un ordre réel, plus une pré-conversion mid).
    #[test]
    fn start_qty_is_notional_usd() {
        let mut e = Engine::new(book_distorted());
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        e.sync(1_000);
        let q = e.start_qty(pi, &pt).unwrap();
        assert_eq!(q, crate::fees::NOTIONAL_USD);
    }
}
