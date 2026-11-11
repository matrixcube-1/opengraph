//! Évaluation DDD à un instant t — port de `poc_engine.Engine.eval_at`.
//!
//! Sémantique marge NETTED (correctif 11/11 — bug panier, prouvé live sur
//! cTrader : 7 séquences legs=1, −13,63 $ de coûts purs) :
//!
//! ```text
//! ok(si)    = quote publiée, non future, âge <= fresh_ms
//! fx_ok(si) = ok(si) ET spread <= spread_cap x spread médian (cap FX)
//! panier    = [USD->s0] + jambes + [end->USD] — chaque conversion est
//!             une position marge réelle (exposition résiduelle ~0)
//! rate      = produit des traversées EXÉCUTABLES de toutes les positions
//!             (entrée + jambes + sortie — jamais de mid, jamais de
//!             liquidation non exécutée)
//! net       = usd_out - NOTIONAL - 2 x comm x nb_pos x comm_scale - unwind
//! unwind    = re-traversée du spread par position à la clôture
//! ```
//!
//! Chemin invalide si : jambe non chargée, jambe non fx_ok, rate/comm/unwind
//! non finis. Retourne l'argmax — premier maximum en cas d'égalité, comme
//! `np.argmax`.

use crate::engine::Engine;
use crate::fees::NOTIONAL_USD;
use crate::paths::PathTables;

/// Résultat d'évaluation : meilleur net, indice de chemin, DDD brut.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct EvalResult {
    /// `usd_out - NOTIONAL - comm` du meilleur chemin.
    pub net_usd: f64,
    pub path_idx: usize,
    /// `usd_out - NOTIONAL` — marge brute avant commissions.
    pub ddd_usd: f64,
}

impl Engine {
    /// Évalue les 2 072 chemins sur l'état courant ; retourne le meilleur
    /// net exécutable, ou None si aucun chemin valide.
    pub fn eval_at(
        &self,
        t: i64,
        fresh_ms: f64,
        spread_cap: f64,
        comm_scale: f64,
        pt: &PathTables,
    ) -> Option<EvalResult> {
        let n = self.data.len();
        let ok: Vec<bool> = (0..n).map(|i| self.ok(i, t, fresh_ms)).collect();
        // spread relatif vs médian — cap appliqué aux arêtes FX uniquement
        let fx_ok: Vec<bool> = (0..n)
            .map(|i| {
                ok[i] && !(self.is_fx[i] && (self.ask[i] - self.bid[i]) > spread_cap * self.med[i])
            })
            .collect();
        // coût de re-traversée du spread à la clôture d'une position ~10K
        let spread_cost = |si: usize| -> f64 {
            let mid = 0.5 * (self.bid[si] + self.ask[si]);
            if mid.is_finite() && mid > 0.0 {
                (self.ask[si] - self.bid[si]) / mid * NOTIONAL_USD
            } else {
                f64::NAN
            }
        };

        let mut best_net = f64::NEG_INFINITY;
        let mut best_pi = 0usize;
        let mut best_ddd = 0.0;

        for pi in 0..pt.len() {
            let s0 = pt.p_s[pi] as usize;
            let end = pt.p_end[pi] as usize;
            let mut rate = 1.0f64;
            let mut comm = 0.0f64;
            let mut unwind = 0.0f64;
            let mut valid = true;

            // entrée USD->s0 : position réelle (direct = achat cUSD à l'ask,
            // inverse = vente USDc au bid) — plus jamais de mid non exécuté.
            if s0 != 0 {
                match self.usd_pair(s0) {
                    Some((se, sd)) if fx_ok[se] => {
                        rate *= if sd { 1.0 / self.ask[se] } else { self.bid[se] };
                        unwind += spread_cost(se);
                        comm += self.comm_per_side;
                    }
                    _ => continue,
                }
            }
            for li in 0..pt.p_l[pi] as usize {
                let e = pt.p_e[pi][li] as usize;
                let Some(si) = self.fx_si[e] else {
                    valid = false;
                    break;
                };
                if !fx_ok[si] {
                    valid = false;
                    break;
                }
                rate *= if pt.p_dir[pi][li] == 1 {
                    self.bid[si]
                } else {
                    1.0 / self.ask[si]
                };
                // clôture réelle de la position marge : re-traversée complète
                // du spread de l'arête (audit marge 10/11 — mesuré live).
                unwind += spread_cost(si);
                comm += self.comm_per_side;
            }
            if !valid {
                continue;
            }
            // sortie end->USD : position réelle (direct = vente cUSD au bid,
            // inverse = achat USDc à l'ask) — plus de marquage usd_liq.
            if end != 0 {
                match self.usd_pair(end) {
                    Some((xe, xd)) if fx_ok[xe] => {
                        rate *= if xd { self.bid[xe] } else { 1.0 / self.ask[xe] };
                        unwind += spread_cost(xe);
                        comm += self.comm_per_side;
                    }
                    _ => continue,
                }
            }
            let usd_out = NOTIONAL_USD * rate;
            // comm aller-retour : ouverture + clôture par position.
            let comm_fx = 2.0 * comm * comm_scale;
            if !(usd_out.is_finite() && comm_fx.is_finite() && unwind.is_finite()) {
                continue;
            }
            let net = usd_out - NOTIONAL_USD - comm_fx - unwind;
            if net > best_net {
                best_net = net;
                best_pi = pi;
                best_ddd = usd_out - NOTIONAL_USD;
            }
        }

        best_net.is_finite().then_some(EvalResult {
            net_usd: best_net,
            path_idx: best_pi,
            ddd_usd: best_ddd,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::SymbolSeries;
    use crate::paths::build_tables;
    use crate::universe::FX;

    /// Livre synthétique cohérent : chaque paire cote le mid implicite
    /// up[base]/up[quote], spread symétrique `half` en fraction.
    fn book(ups: [f64; 8], half: f64, distort: Option<(&str, f64)>) -> Vec<SymbolSeries> {
        use crate::universe::ci;
        FX.iter()
            .map(|&s| {
                let a = ci(&s[0..3]) as usize;
                let b = ci(&s[3..6]) as usize;
                let mut mid = ups[a] / ups[b];
                if let Some((ds, m)) = distort {
                    if ds == s {
                        mid *= m;
                    }
                }
                let bid = mid * (1.0 - half);
                let ask = mid * (1.0 + half);
                SymbolSeries {
                    sym: s,
                    ts: vec![1_000],
                    bid: vec![bid],
                    ask: vec![ask],
                    avol: vec![1.0],
                    bvol: vec![1.0],
                }
            })
            .collect()
    }

    fn ups() -> [f64; 8] {
        // USD=1 ; les autres : valeurs de mid USD réalistes
        [
            1.0,
            1.10,
            1.30,
            1.0 / 150.0,
            1.0 / 0.90,
            1.0 / 1.35,
            0.65,
            0.60,
        ]
    }

    /// Livre cohérent : aucune opportunité positive après commissions.
    #[test]
    fn eval_no_opportunity_on_consistent_book() {
        let e = Engine::new(book(ups(), 1e-4, None));
        let mut e = e;
        e.sync(1_500);
        let pt = build_tables();
        let r = e.eval_at(1_500, 5_000.0, 10.0, 1.0, &pt).unwrap();
        assert!(r.net_usd < 0.0, "livre cohérent: net devrait être < 0");
    }

    /// Distorsion CADJPY -0,5 % sur l'ask -> chemin JPY->CAD gagnant.
    /// Net attendu recalculé indépendamment dans le test.
    #[test]
    fn eval_finds_distorted_edge() {
        let ups = ups();
        // mid CADJPY = up[CAD]/up[JPY] = (1/1.35)/(1/150) ; ask *0.995
        let e = Engine::new(book(ups, 1e-4, Some(("CADJPY", 0.995))));
        let mut e = e;
        e.sync(1_500);
        let pt = build_tables();
        let r = e.eval_at(1_500, 5_000.0, 10.0, 1.0, &pt).unwrap();

        // le chemin gagnant exploite la traversée JPY->CAD sur CADJPY.
        // Panier exécuté : USDJPY + CADJPY + USDCAD (3 positions).
        // À panier identique et coûts identiques, le DFS depuis s0=USD
        // enregistre [USDJPY, CADJPY] avant [CADJPY]@JPY/[CADJPY,USDCAD].
        let path = &pt.paths[r.path_idx];
        let i_uj = FX.iter().position(|&s| s == "USDJPY").unwrap() as u8;
        let i_cj = FX.iter().position(|&s| s == "CADJPY").unwrap() as u8;
        assert_eq!(path.len(), 2);
        assert_eq!(path[0], i_uj);
        assert_eq!(path[1], i_cj);
        assert_eq!(pt.p_dir[r.path_idx][0], 1);
        assert_eq!(pt.p_dir[r.path_idx][1], -1);
        assert_eq!(pt.p_s[r.path_idx], 0); // s0 = USD
        assert_eq!(pt.p_end[r.path_idx], 5); // end = CAD

        // net attendu (sémantique panier) :
        //   rate = bid(USDJPY) x (1/ask_CADJPY) x (1/ask_USDCAD)
        //   comm = 2 x 3 positions x comm_usd ; unwind = 3 x 2e-4 x N
        let mid_cadjpy = ups[5] / ups[3] * 0.995;
        let ask_cadjpy = mid_cadjpy * 1.0001;
        let bid_usdjpy = (ups[0] / ups[3]) * (1.0 - 1e-4);
        let ask_usdcad = (ups[0] / ups[5]) * (1.0 + 1e-4);
        let rate = bid_usdjpy * (1.0 / ask_cadjpy) * (1.0 / ask_usdcad);
        let usd_out = crate::fees::NOTIONAL_USD * rate;
        let comm = 2.0 * 3.0 * crate::fees::comm_usd();
        let unwind = 3.0 * 2e-4 * crate::fees::NOTIONAL_USD;
        let expected = usd_out - crate::fees::NOTIONAL_USD - comm - unwind;
        assert!((r.net_usd - expected).abs() < 1e-6);
        assert!(r.net_usd > 0.0);
    }

    /// T2/T3 : symbole sans tick publié -> ses chemins inéligibles.
    #[test]
    fn eval_rejects_stale_paths() {
        let mut series = book(ups(), 1e-4, Some(("CADJPY", 0.995)));
        // AUDUSD sans aucun tick : ses chemins doivent être invalides
        for s in &mut series {
            if s.sym == "AUDUSD" {
                s.ts.clear();
                s.bid.clear();
                s.ask.clear();
                s.avol.clear();
                s.bvol.clear();
            }
        }
        let e = Engine::new(series);
        let mut e = e;
        e.sync(1_500);
        let pt = build_tables();
        let r = e.eval_at(1_500, 5_000.0, 10.0, 1.0, &pt);
        // AUDUSD jamais vu -> up[AUD] NaN -> tout chemin AUD invalide,
        // mais CADJPY (distordu) reste le meilleur net positif.
        if let Some(r) = r {
            for li in 0..pt.p_l[r.path_idx] as usize {
                let e_idx = pt.p_e[r.path_idx][li] as usize;
                assert_ne!(FX[e_idx], "AUDUSD");
            }
        }
    }

    /// spread_cap : spread dilaté -> jambe invalidée.
    #[test]
    fn eval_spread_cap_blocks_wide_quotes() {
        let mut series = book(ups(), 1e-4, Some(("CADJPY", 0.995)));
        for s in &mut series {
            if s.sym == "CADJPY" {
                // ticks normaux puis un tick à spread dilaté : le médian
                // reste le spread normal -> le tick courant viole le cap
                s.ts = vec![900, 1_000, 1_100];
                let m = s.bid[0] / (1.0 - 1e-4);
                s.bid = vec![m * 0.9999, m * 0.9999, m * 0.9999];
                s.ask = vec![m * 1.0001, m * 1.0001, m * 1.01];
                s.avol = vec![1.0; 3];
                s.bvol = vec![1.0; 3];
            }
        }
        let e = Engine::new(series);
        let mut e = e;
        e.sync(1_500);
        let pt = build_tables();
        let r = e.eval_at(1_500, 5_000.0, 1.5, 1.0, &pt).unwrap();
        // CADJPY exclu par le cap : le meilleur net ne peut pas l'utiliser
        for li in 0..pt.p_l[r.path_idx] as usize {
            assert_ne!(FX[pt.p_e[r.path_idx][li] as usize], "CADJPY");
        }
    }
}
