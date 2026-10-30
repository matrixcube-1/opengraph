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
//! Abort : `pnl = -12 x filled + cash - comm` (proxy de débouclage).
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
        lat_ms: f64,
        tau_slip_bp: f64,
        q_min: f64,
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
        let slip = tau_slip_bp * 1e-4;

        macro_rules! abort {
            ($why:expr) => {
                return Some(ExecOutcome::Abort {
                    reason: $why,
                    pnl_usd: -(ABORT_COST_PER_LEG_USD * filled as f64) + cash - comm,
                    filled_legs: filled,
                    partial_legs: parts,
                    dur_ms: t - t0 as f64,
                })
            };
        }

        for &e in legs {
            let si = self.fx_si[e as usize].expect("filtré ci-dessus");
            let (a, b) = Self::edge_nodes(e);
            let d: i8 = if node == a as usize { 1 } else { -1 };
            let t_f = t + lat_ms;

            let q_send = match self.quote_at_f(si, t) {
                Some((_, bid, ask, _)) => {
                    if d == 1 {
                        bid
                    } else {
                        1.0 / ask
                    }
                }
                None => f64::NAN,
            };
            let Some((_, bid_f, ask_f, vol_f)) = self.quote_at_f(si, t_f) else {
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

            // garde volume top-of-book (spécifiée ici ; absente du POC Python)
            let vol_base = vol_f * 1e6;
            let need_base = if d == 1 { qty } else { qty * q_now };
            let frac_vol = if vol_base.is_finite() && vol_base > 0.0 && need_base > 0.0 {
                (vol_base / need_base).min(1.0)
            } else {
                0.0
            };
            frac = frac.min(frac_vol);

            if frac < q_min {
                abort!(AbortReason::BelowQMin);
            }
            if frac < 1.0 {
                cash += qty * (1.0 - frac) * up[node];
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

        let usd_end = qty * up[node];
        Some(ExecOutcome::Done {
            pnl_usd: usd_end + cash - NOTIONAL_USD - comm,
            filled_legs: filled,
            partial_legs: parts,
            dur_ms: t - t0 as f64,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::SymbolSeries;
    use crate::paths::build_tables;
    use crate::universe::{ci, FX};

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
                    vol: vec![1.0; 2],
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
        let r = e.exec_seq(1_000, pi, 2.5, 0.8, 0.5, &pt).expect("exec");
        let ExecOutcome::Done {
            pnl_usd,
            dur_ms,
            filled_legs,
            partial_legs,
        } = r
        else {
            panic!("attendu Done");
        };
        // qty JPY -> CAD à 1/ask ; marquage up[CAD] ; comm = 3/up[CAD]
        let mid = ups[5] / ups[3] * 0.995;
        let ask = mid * 1.0001;
        let qty = 100_000.0 / ups[3] / ask;
        let expected = qty * ups[5] - 100_000.0 - COMM_PER_SIDE_USD / ups[5];
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
        let r = e.exec_seq(1_000, pi, 2.5, 0.8, 0.5, &pt).unwrap();
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
        let r = e.exec_seq(1_000, pi, 2.5, 0.8, 0.5, &pt).unwrap();
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
                s.vol[1] = 0.05; // 50 K CAD affichés < ~74 K requis
            }
        }
        let mut e = Engine::new(series);
        let pt = build_tables();
        let pi = path_jpy_cad(&pt);
        let r = e.exec_seq(1_000, pi, 2.5, 0.8, 0.5, &pt).unwrap();
        let ExecOutcome::Abort { reason, .. } = r else {
            panic!("attendu Abort (liquidite insuffisante)");
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
        let r = e.exec_seq(1_000, pi, 2.5, 0.8, 0.5, &pt).unwrap();
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
}
