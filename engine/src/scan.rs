//! Scan événementiel + rejouabilité — ports de `Engine.scan` et
//! `poc_optuna.replay`.
//!
//! - `scan` : boucle sur les ticks FX triés ; n'évalue que quand le mid du
//!   symbole tické a bougé > `move_gate x spread médian` depuis la dernière
//!   éval, ou toutes les `eval_every_ms`. Produit les candidats (net >
//!   `thresh`) et les épisodes (fenêtre contiguë net > 0).
//! - `replay` : ré-évalue chaque candidat sous les paramètres du trial
//!   (la détection est refaite — le scan est un sur-ensemble généreux),
//!   exécute `exec_seq`, verrou `t_free` = dur + cooldown.

use crate::engine::Engine;
use crate::eval::EvalResult;
use crate::exec::ExecOutcome;
use crate::paths::PathTables;

/// Paramètres calibrables — le jeu complet du POC Optuna.
/// `vol_guard` : drapeau hors-POC — garde volume top-of-book dans
/// `exec_seq` (false = parité stricte POC ; true = spec Rust production).
#[derive(Clone, Copy, Debug)]
pub struct Params {
    pub fresh_ms: f64,
    pub spread_cap: f64,
    pub comm_scale: f64,
    pub ddd_min: f64,
    pub lat_ms: f64,
    pub tau_slip_bp: f64,
    pub q_min: f64,
    pub cooldown_s: f64,
    pub vol_guard: bool,
}

/// Défauts du document (`DOC_DEFAULTS` du POC — baseline enqueued).
/// `vol_guard = false` : parité stricte avec le POC Python.
pub const DOC_DEFAULTS: Params = Params {
    fresh_ms: 500.0,
    spread_cap: 1.7,
    comm_scale: 1.0,
    ddd_min: 0.0,
    lat_ms: 2.5,
    tau_slip_bp: 0.8,
    q_min: 0.5,
    cooldown_s: 0.0,
    vol_guard: false,
};

/// Candidat de détection : `(t, chemin, net, ddd)`.
#[derive(Clone, Copy, Debug)]
pub struct Cand {
    pub t_ms: i64,
    pub path_idx: usize,
    pub net: f64,
    pub ddd: f64,
}

/// Épisode d'opportunité : fenêtre contiguë `net > 0`.
/// `(t0, t1, chemin à l'ouverture, net max)`.
#[derive(Clone, Copy, Debug)]
pub struct Episode {
    pub t0: i64,
    pub t1: i64,
    pub path_idx: usize,
    pub net_max: f64,
}

/// Statistiques de scan (comptage POC `n_ev`/`n_eval`).
#[derive(Default, Debug)]
pub struct ScanStats {
    pub n_events: u64,
    pub n_evals: u64,
}

impl Engine {
    /// Passe unique sur les événements FX.
    pub fn scan(
        &mut self,
        pt: &PathTables,
        fresh_ms: f64,
        spread_cap: f64,
        thresh: f64,
        move_gate: f64,
        eval_every_ms: i64,
    ) -> (Vec<Cand>, Vec<Episode>, ScanStats) {
        let mut cands = Vec::new();
        let mut episodes = Vec::new();
        let mut open_ep: Option<Episode> = None;
        let mut last_eval_t = i64::MIN;
        let mut eval_mid = vec![f64::NAN; self.data.len()];
        let mut stats = ScanStats::default();

        for k in 0..self.ev.len() {
            let (t, i) = self.ev[k];
            self.tick(i, t);
            stats.n_events += 1;
            let mid = 0.5 * (self.bid[i] + self.ask[i]);
            let moved =
                !eval_mid[i].is_finite() || (mid - eval_mid[i]).abs() > move_gate * self.med[i];
            if !moved && t - last_eval_t < eval_every_ms {
                continue;
            }
            last_eval_t = t;
            eval_mid = (0..self.data.len())
                .map(|j| 0.5 * (self.bid[j] + self.ask[j]))
                .collect();
            stats.n_evals += 1;
            let Some(EvalResult {
                net_usd: net,
                path_idx: pi,
                ddd_usd: ddd,
            }) = self.eval_at(t, fresh_ms, spread_cap, 1.0, pt)
            else {
                // POC : eval None -> continue SANS fermer l'épisode ouvert
                continue;
            };
            if net > thresh {
                cands.push(Cand {
                    t_ms: t,
                    path_idx: pi,
                    net,
                    ddd,
                });
            }
            if net > 0.0 {
                match &mut open_ep {
                    None => {
                        open_ep = Some(Episode {
                            t0: t,
                            t1: t,
                            path_idx: pi,
                            net_max: net,
                        })
                    }
                    Some(ep) => ep.net_max = ep.net_max.max(net),
                }
            } else if let Some(ep) = open_ep.take() {
                episodes.push(Episode { t1: t, ..ep });
            }
        }
        if let Some(ep) = open_ep.take() {
            episodes.push(Episode {
                t1: self.ev.last().map(|e| e.0).unwrap_or(0),
                ..ep
            });
        }
        (cands, episodes, stats)
    }

    /// Rejoue les candidats sous les paramètres du trial.
    /// Même sémantique que `poc_optuna.replay` : détection ré-évaluée,
    /// verrou anti-rechevauchement `t_free`.
    pub fn replay(&mut self, cands: &[Cand], p: &Params, pt: &PathTables) -> ReplayResult {
        let mut pnl = 0.0f64;
        let mut n = 0u64;
        let mut aborts = 0u64;
        let mut wins = 0u64;
        let mut durs = Vec::new();
        let mut t_free = f64::NEG_INFINITY;
        for c in cands {
            if (c.t_ms as f64) < t_free {
                continue;
            }
            self.sync(c.t_ms);
            let ev = self.eval_at(c.t_ms, p.fresh_ms, p.spread_cap, p.comm_scale, pt);
            let Some(ev) = ev.filter(|e| e.net_usd >= p.ddd_min) else {
                continue;
            };
            let ep = crate::exec::ExecParams {
                lat_ms: p.lat_ms,
                tau_slip_bp: p.tau_slip_bp,
                q_min: p.q_min,
                vol_guard: p.vol_guard,
            };
            let Some(r) = self.exec_seq(c.t_ms, ev.path_idx, &ep, pt) else {
                continue;
            };
            if let ExecOutcome::Abort { .. } = r {
                aborts += 1;
            }
            if r.pnl() > 0.0 {
                wins += 1;
            }
            pnl += r.pnl();
            n += 1;
            durs.push(r.dur_ms());
            t_free = c.t_ms as f64 + r.dur_ms().max(1.0) + p.cooldown_s * 1000.0;
        }
        ReplayResult {
            pnl,
            trades: n,
            aborts,
            win_rate: if n > 0 { wins as f64 / n as f64 } else { 0.0 },
            dur_ms_mean: if durs.is_empty() {
                0.0
            } else {
                durs.iter().sum::<f64>() / durs.len() as f64
            },
        }
    }
}

/// Agrégat d'un replay — miroir du dict retourné par le POC.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ReplayResult {
    pub pnl: f64,
    pub trades: u64,
    pub aborts: u64,
    pub win_rate: f64,
    pub dur_ms_mean: f64,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::SymbolSeries;
    use crate::paths::build_tables;
    use crate::universe::{ci, FX};

    fn book_ts(shift_cadjpy: usize) -> Vec<SymbolSeries> {
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
                let n_ticks = 4;
                let mut ts = Vec::with_capacity(n_ticks);
                let mut bids = Vec::with_capacity(n_ticks);
                let mut asks = Vec::with_capacity(n_ticks);
                for k in 0..n_ticks {
                    // CADJPY se déforme à partir du tick `shift_cadjpy`
                    let m = if s == "CADJPY" && k >= shift_cadjpy {
                        mid * 0.995
                    } else {
                        mid
                    };
                    ts.push(1_000 + 100 * k as i64);
                    bids.push(m * 0.9999);
                    asks.push(m * 1.0001);
                }
                let _ = &mut mid;
                SymbolSeries {
                    sym: s,
                    ts,
                    bid: bids,
                    ask: asks,
                    vol: vec![1.0; n_ticks],
                }
            })
            .collect()
    }

    /// Le scan détecte l'ouverture de la distorsion : candidats + épisode.
    #[test]
    fn scan_detects_distortion_episode() {
        let mut e = Engine::new(book_ts(2));
        let pt = build_tables();
        let (cands, eps, st) = e.scan(&pt, 5_000.0, 10.0, -30.0, 0.25, 500);
        assert!(st.n_events > 0 && st.n_evals > 0);
        assert!(!cands.is_empty());
        assert!(!eps.is_empty());
        assert!(eps.iter().any(|ep| ep.net_max > 0.0));
    }

    /// T6 (parité) : rejouer les mêmes candidats deux fois sur le même
    /// engine donne un résultat identique — pas de fuite d'état.
    #[test]
    fn replay_is_deterministic_across_calls() {
        let mut e = Engine::new(book_ts(2));
        let pt = build_tables();
        let (cands, _, _) = e.scan(&pt, 5_000.0, 10.0, -30.0, 0.25, 500);
        let r1 = e.replay(&cands, &DOC_DEFAULTS, &pt);
        let r2 = e.replay(&cands, &DOC_DEFAULTS, &pt);
        assert_eq!(r1, r2, "fuite d'état inter-replays");
    }

    /// Cooldown : verrou anti-rechevauchement réduit les trades.
    #[test]
    fn replay_cooldown_limits_trades() {
        let mut e = Engine::new(book_ts(2));
        let pt = build_tables();
        let (cands, _, _) = e.scan(&pt, 5_000.0, 10.0, -30.0, 0.25, 500);
        let p_no = DOC_DEFAULTS;
        let p_cd = Params {
            cooldown_s: 60.0,
            ..DOC_DEFAULTS
        };
        let r1 = e.replay(&cands, &p_no, &pt);
        let r2 = e.replay(&cands, &p_cd, &pt);
        assert!(r2.trades <= r1.trades);
    }
}
