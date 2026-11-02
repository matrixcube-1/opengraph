//! Exécution séquentielle E1-E9 — port de `poc_engine.Engine.exec_seq`.
//!
//! Chaque jambe, en séquence :
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
//! partiel : cash += qty(1-frac) x up[node] (marquage mid)
//! qty *= frac x q_now ; comm += COMM/up[base] ; node avance
//! ```
//!
//! Fin : `pnl = qty x up[node] + cash - NOTIONAL - comm`.
//! Abort : `pnl = -abort_cost + cash - comm` où abort_cost = somme par
//! jambe remplie de `med_rel x notional + comm` (débouclage IOC croisant
//! le spread médian — audit OG2-D2).
//!
//! ## Garde volume top-of-book (ajout spécifié vs POC)
//!
//! `vol` Dukas FX = millions de devise de base. La jambe transite `need_base`
//! unités de base (qty si vente de base, qty x q_now si achat de base) ;
//! si le volume affiché au tick de fill n'y suffit pas, `frac` est plafonné.
//! `vol <= 0` ou non fini = aucune liquidité affichée -> frac 0 -> rejet.
//! Effet quasi nul à 100 K$ sur FX (~1-3 M affichés) mais testé.

use crate::engine::Engine;
use crate::fees::{ABORT_COST_PER_LEG_USD, COMM_PER_SIDE_USD, NOTIONAL_USD};
use crate::paths::PathTables;

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
        let legs = &pt.paths[pi];
        for &e in legs {
            self.fx_si[e as usize]?; // donnée absente -> skip (None)
        }
        self.sync(t0);
        // POC : usd_price calculé sur TOUS les mids courants (ok = ones) —
        // le marquage résiduel ne subit pas la garde de fraîcheur.
        let ok_all = vec![true; self.data.len()];
        let up = self.usd_price(&ok_all);
        // Convention A stricte : résiduel -> taux de liquidation exécutable.
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
                COMM_PER_SIDE_USD / up[node]
            } else {
                COMM_PER_SIDE_USD
            };
            (r, c)
        };
        let start = pt.p_s[pi] as usize;
        if !up[start].is_finite() || up[start] <= 0.0 {
            return None; // Python produirait pnl NaN ; refus explicite
        }
        let mut qty = NOTIONAL_USD / up[start];
        let mut cash = 0.0f64;
        let mut comm = 0.0f64;
        let mut t = t0 as f64;
        let mut filled = 0u8;
        let mut parts = 0u8;
        let mut node = start;
        let slip = ep.tau_slip_bp * 1e-4;

        macro_rules! abort {
            ($why:expr) => {
                return Some(ExecOutcome::Abort {
                    reason: $why,
                    pnl_usd: -(self.abort_cost(legs, filled)) + cash - comm,
                    filled_legs: filled,
                    partial_legs: parts,
                    dur_ms: t - t0 as f64,
                })
            };
        }

        for &e in legs {
            let si = self.fx_si[e as usize].expect("filtré ci-dessus");
            let (a, b) = Self::edge_nodes(e);
            // audit OG2-B2 : invariant figé par l'assert de build_tables —
            // un nœud hors arête ne doit JAMAIS tomber dans le else -1.
            debug_assert!(
                node == a as usize || node == b as usize,
                "nœud {node} hors arête ({a},{b})"
            );
            let d: i8 = if node == a as usize { 1 } else { -1 };
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
            comm += if up[a as usize].is_finite() {
                COMM_PER_SIDE_USD / up[a as usize]
            } else {
                COMM_PER_SIDE_USD
            };
            filled += 1;
            node = if node == a as usize {
                b as usize
            } else {
                a as usize
            };
            t = t_f;
        }

        let (r_end, c_end) = liq(node);
        comm += c_end;
        let usd_end = qty * r_end;
        Some(ExecOutcome::Done {
            pnl_usd: usd_end + cash - NOTIONAL_USD - comm,
            filled_legs: filled,
            partial_legs: parts,
            dur_ms: t - t0 as f64,
        })
    }

    /// Quantité de départ en devise `p_s[pi]` — `NOTIONAL/up[start]` sur
    /// l'état COURANT (live : jamais de `sync`, qui rembobinerait les prix
    /// — audit C3). En replay, appeler `sync(t0)` avant.
    /// Retourne None si la marque n'est pas finie positive (Python : NaN).
    pub fn start_qty(&mut self, pi: usize, pt: &PathTables) -> Option<f64> {
        let legs = &pt.paths[pi];
        for &e in legs {
            self.fx_si[e as usize]?;
        }
        let ok_all = vec![true; self.data.len()];
        let up = self.usd_price(&ok_all);
        let u = up[pt.p_s[pi] as usize];
        (u.is_finite() && u > 0.0).then(|| NOTIONAL_USD / u)
    }

    /// Boucle une séquence exécutée par une venue externe (ordres IOC
    /// réels NautilusTrader) — **même arithmétique de bouclage** que
    /// `exec_seq`, appliquée aux fills constatés plutôt qu'au modèle E1-E9.
    ///
    /// `fills` = `(frac_eff, px_node)` par jambe **complétée**, dans
    /// l'ordre du chemin, où :
    /// - `frac_eff` = fraction de la devise nœud effectivement convertie
    ///   (d=+1 : `filled_base/order_base` ; d=-1 : `filled_base*px_fill/acc`) ;
    /// - `px_node` = taux nœud→nœud (`px_fill` si d=+1, `1/px_fill` si d=-1).
    ///
    /// `aborted` : la séquence s'est arrêtée avant la dernière jambe
    /// (kill IOC / q<q_min / no quote) → `pnl = -abort_cost + cash - comm`,
    /// la jambe avortée ne contribue rien (conservatisme POC, identique à
    /// `exec_seq` : ni comm ni produit de la jambe fautive).
    ///
    /// Retourne `(pnl_usd, filled_legs, partial_legs, resid_usd)` où
    /// Coût de débouclage des jambes remplies — audit OG2-D2 : le proxy
    /// fixe 12 $/jambe sous-estimait ~2× le coût réel sur les paires à
    /// spread large (NZDJPY ~2 bp → ~23 $). Pour chaque jambe remplie,
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
                    med / mid * NOTIONAL_USD + COMM_PER_SIDE_USD
                } else {
                    ABORT_COST_PER_LEG_USD
                }
            })
            .sum()
    }

    /// `resid_usd` = valeur de la position restante marquée à la liquidation
    /// EXÉCUTABLE (Convention A stricte) sur l'état COURANT — jamais de
    /// `sync(t0)` en live (audit C3 : rembobiner publiait des prix périmés).
    /// En replay, appeler `sync(t0)` avant pour l'état de départ.
    /// None si la marque de départ manque.
    pub fn exec_settle(
        &mut self,
        pi: usize,
        fills: &[(f64, f64)],
        aborted: bool,
        pt: &PathTables,
    ) -> Option<(f64, u8, u8, f64)> {
        let legs = &pt.paths[pi];
        // audit OG2-B5 : plus de fills que de jambes -> legs[li] hors borne.
        if fills.len() > legs.len() {
            return None;
        }
        for &e in legs {
            self.fx_si[e as usize]?;
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
                COMM_PER_SIDE_USD / up[node]
            } else {
                COMM_PER_SIDE_USD
            };
            (r, c)
        };
        let mut node = pt.p_s[pi] as usize;
        if !up[node].is_finite() || up[node] <= 0.0 {
            return None;
        }
        let mut qty = NOTIONAL_USD / up[node];
        let mut cash = 0.0f64;
        let mut comm = 0.0f64;
        let mut filled = 0u8;
        let mut parts = 0u8;

        for (li, &(frac, px)) in fills.iter().enumerate() {
            let e = legs[li];
            let (a, b) = Self::edge_nodes(e);
            if frac < 1.0 {
                let (r, c) = liq(node);
                cash += qty * (1.0 - frac) * r;
                comm += c;
                qty *= frac;
                parts += 1;
            }
            qty *= px;
            comm += if up[a as usize].is_finite() {
                COMM_PER_SIDE_USD / up[a as usize]
            } else {
                COMM_PER_SIDE_USD
            };
            filled += 1;
            node = if node == a as usize {
                b as usize
            } else {
                a as usize
            };
        }

        // Liquidation exécutable du résiduel — seulement si une position
        // existe réellement (abort à 0 fill : rien n'a été acheté).
        let (r_end, c_end) = liq(node);
        let resid_usd = if filled > 0 {
            comm += c_end;
            qty * r_end
        } else {
            0.0
        };
        let pnl = if aborted {
            -(self.abort_cost(legs, filled)) + cash - comm
        } else {
            resid_usd + cash - NOTIONAL_USD - comm
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
        // qty JPY -> CAD à 1/ask ; marquage EXÉCUTABLE CAD->USD (1/ask
        // USDCAD = 1/(1.35*1.0001)) ; comm jambe 3/up[CAD] + comm liq 3.
        let mid = ups[5] / ups[3] * 0.995;
        let ask = mid * 1.0001;
        let qty = 100_000.0 / ups[3] / ask;
        let usdcad_ask = 1.35 * 1.0001;
        let expected = qty / usdcad_ask - 100_000.0
            - COMM_PER_SIDE_USD / ups[5] - COMM_PER_SIDE_USD;
        assert!((pnl_usd - expected).abs() < 1e-6);
        assert_eq!(dur_ms, 2.5);
        assert_eq!(filled_legs, 1);
        assert_eq!(partial_legs, 0);
        assert!(pnl_usd > 0.0);
    }

    /// Prix adverse > 3.slip entre envoi et fill -> kill IOC, coût 0 jambe.
    #[test]
    fn exec_kill_ioc_on_adverse_move() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                s.ask[1] *= 1.01; // +1 % au fill -> move >> 3x0.8bp
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
        assert_eq!(filled_legs, 0);
        assert_eq!(pnl_usd, 0.0); // -12 x 0 + cash 0 - comm 0
    }

    /// Pas de tick <= t_fill -> abort "no quote".
    #[test]
    fn exec_no_quote_aborts() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                s.ts[1] = 1_006; // plus de tick <= 1002.5
                s.bid[1] = s.bid[0];
                s.ask[1] = s.ask[0];
            }
        }
        // hmm: le tick à 1000 reste <= 1002.5 -> il faut décaler les deux
        for s in &mut series {
            if s.sym == "CADJPY" {
                s.ts = vec![1_006, 1_007];
            }
        }
        let mut e = Engine::new(series);
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let r = e.exec_seq(1_000, pi, &EP, &pt).unwrap();
        assert_eq!(
            r,
            ExecOutcome::Abort {
                reason: AbortReason::NoQuote,
                pnl_usd: 0.0,
                filled_legs: 0,
                partial_legs: 0,
                dur_ms: 0.0,
            }
        );
    }

    /// Garde volume : tick de fill avec volume insuffisant -> rejet q<q_min.
    #[test]
    fn exec_volume_guard_rejects_thin_book() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                s.avol[1] = 0.05; // 50 K CAD no ask < ~74 K requis (jambe buy)
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
                s.avol[1] = 0.05; // ask fino (lado que a compra atinge)
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

    /// Mouvement intermédiaire -> fill partiel 60 %, résiduel au cash.
    #[test]
    fn exec_partial_fill_marks_residual() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                // move ~1.5 bp entre send et fill -> frac 0.6 (slip=0.8bp)
                s.ask[1] *= 1.0 + 1.5e-4;
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
        assert_eq!(filled_legs, 1);
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
    /// mêmes marquages cash/comm.
    #[test]
    fn settle_full_fills_matches_exec_seq_3_legs() {
        let mut e = Engine::new(book_distorted());
        let pt = build_tables();
        let pi = path_n_legs(&pt, 3);
        // fills externes = fill complet à la quote de t0 (lat=0 : q_now
        // = quote au temps de décision pour chaque jambe)
        let mut fills = Vec::new();
        let mut node = pt.p_s[pi];
        for &e_i in &pt.paths[pi] {
            let si = e.fx_si[e_i as usize].unwrap();
            let (a, b) = Engine::edge_nodes(e_i);
            let (_, bid, ask, _, _) = e.quote_at_f(si, 1_000.0).unwrap();
            let px = if node == a { bid } else { 1.0 / ask };
            fills.push((1.0, px));
            node = if node == a { b } else { a };
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
        assert_eq!(filled_s, 3);
        assert_eq!(parts_s, 0);
        assert_eq!(filled_legs, 3);
        assert_eq!(partial_legs, 0);
        // même ordre d'opérations f64 -> égalité quasi bit à bit
        assert!((pnl_s - pnl_usd).abs() / pnl_usd.abs().max(1.0) < 1e-12);
    }

    /// Audit OG2-B5 : plus de fills que de jambes -> None, pas de panic
    /// index hors borne.
    #[test]
    fn settle_rejects_more_fills_than_legs() {
        let mut e = Engine::new(book_distorted());
        let pt = build_tables();
        let pi = path_jpy_cad(&pt); // chemin à 1 jambe
        e.sync(1_000);
        // 2 fills sur 1 jambe : avant = panic legs[1] hors borne
        assert!(e
            .exec_settle(pi, &[(1.0, 1.0), (1.0, 1.0)], false, &pt)
            .is_none());
    }

    /// Abort après jambes complétées : pnl = -abort_cost + cash - comm
    /// avec abort_cost = med_rel x notional + comm par jambe remplie
    /// (audit OG2-D2) ; la jambe avortée ne contribue rien.
    #[test]
    fn settle_abort_matches_hand_computation() {
        let mut e = Engine::new(book_distorted());
        let pt = build_tables();
        let pi = path_jpy_cad(&pt); // 1 jambe JPY->CAD, filled=0
        e.sync(1_000);
        let (pnl, filled, parts, _resid) = e.exec_settle(pi, &[], true, &pt).unwrap();
        assert_eq!((pnl, filled, parts), (0.0, 0, 0)); // cout 0 x filled

        // même jambe mais remplie puis abort « impossible » : pour un
        // chemin à 1 jambe filled=1 -> -cout(jambe) + 0 - comm
        // (d=-1 -> px_node 1/ask)
        let si = e.fx_si[pt.paths[pi][0] as usize].unwrap();
        let (_, bid_l, ask, _, _) = e.quote_at_f(si, 1_000.0).unwrap();
        let (pnl2, filled2, _, resid2) = e
            .exec_settle(pi, &[(1.0, 1.0 / ask)], true, &pt)
            .unwrap();
        assert_eq!(filled2, 1);
        let up_cad = 1.0 / 1.35; // up[CAD] du fixture (ups[5])
        // comm = jambe CADJPY (COMM/up[CAD]) + liquidation CAD->USD (COMM,
        // paire inverse USDCAD -> base USD)
        let comm = crate::fees::COMM_PER_SIDE_USD / up_cad
            + crate::fees::COMM_PER_SIDE_USD;
        // coût de débouclage par arête : med_rel x notional + comm —
        // la fixture a un spread relatif de 2 bp -> ~23 $ (avant : 12 $ fixe)
        let abort_cost = e.med[si] / ((bid_l + ask) * 0.5)
            * crate::fees::NOTIONAL_USD
            + crate::fees::COMM_PER_SIDE_USD;
        assert!((abort_cost - 23.0).abs() < 1.0);
        assert!((pnl2 - (-abort_cost - comm)).abs() < 1e-9);
        // resid_usd = position abandonnée marquée exécutable : 15M JPY ->
        // CAD liquidés à 1/ask(USDCAD)
        assert!((resid2 - 15e6 / ask / (1.35 * 1.0001)).abs() < 1e-6);
    }

    /// Partiel 60 % via settle = même résiduel cash que exec_seq partiel.
    #[test]
    fn settle_partial_marks_residual_like_exec_seq() {
        let mut series = book_distorted();
        for s in &mut series {
            if s.sym == "CADJPY" {
                s.ask[1] *= 1.0 + 1.5e-4;
            }
        }
        let mut e = Engine::new(series);
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let ep = ExecParams { lat_ms: 2.5, ..EP0 };
        let ExecOutcome::Done { pnl_usd, .. } = e.exec_seq(1_000, pi, &ep, &pt).unwrap() else {
            panic!("attendu Done");
        };
        // fill constaté : frac 0.6 à la quote du fill (tick 1002)
        let si = e.fx_si[pt.paths[pi][0] as usize].unwrap();
        let (_, _, ask_f, _, _) = e.quote_at_f(si, 1_002.0).unwrap();
        e.sync(1_000);
        let (pnl_s, _, parts_s, residp) = e
            .exec_settle(pi, &[(0.6, 1.0 / ask_f)], false, &pt)
            .unwrap();
        assert_eq!(parts_s, 1);
        assert!((pnl_s - pnl_usd).abs() / pnl_usd.abs().max(1.0) < 1e-12);
        // resid_usd = qty_end(=15M*0.6/ask_f) marqué exécutable (1/ask USDCAD)
        assert!((residp - 15e6 * 0.6 / ask_f / (1.35 * 1.0001)).abs() < 1e-6);
    }

    /// `start_qty` = NOTIONAL / up[start] — JPY : 100 k x 150.
    #[test]
    fn start_qty_uses_usd_mark_at_t0() {
        let mut e = Engine::new(book_distorted());
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        e.sync(1_000);
        let q = e.start_qty(pi, &pt).unwrap();
        assert!((q - 100_000.0 * 150.0).abs() < 1e-6);
    }
}
