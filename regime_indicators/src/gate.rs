//! Porte dure à hystérésis + modulation gπ — conception §4–5.
//!
//! Fermeture immédiate si T̃ > θ_T ou D > θ_D (asymétrie volontaire) ;
//! réouverture si calme strict (bande δ) et durable (τ_hold).
//! Indisponible ⇒ fermé (fail-closed). État initial : fermé.
//! Battement borné : ≤ ⌈Δ/τ_hold⌉ réouvertures par intervalle.

/// Paramètres calibrés par Optuna (domaines imposés).
#[derive(Debug, Clone, Copy)]
pub struct GateParams {
    /// θ_T : seuil de tension (unités de z).
    pub theta_t: f64,
    /// δ_T : bande de réouverture sous θ_T.
    pub hyst_t: f64,
    /// θ_D ∈ [1/7, 1].
    pub theta_d: f64,
    /// δ_D : bande de réouverture sous θ_D.
    pub hyst_d: f64,
    /// τ_hold : durée de calme exigée avant réouverture (ns).
    pub hold_ns: u64,
}

/// Automate d'hystérésis (Éq. 11).
pub struct Gate {
    pub p: GateParams,
    open: bool,
    calm_since: Option<u64>,
}

impl Gate {
    pub fn new(p: GateParams) -> Self {
        Self {
            p,
            open: false,
            calm_since: None,
        }
    }

    pub fn is_open(&self) -> bool {
        self.open
    }

    /// Met à jour l'automate à l'instant t_ns.
    /// `t_tilde`/`dom` = None ou non fini ⇒ fermé (fail-closed).
    /// Retourne l'état de la porte après mise à jour.
    pub fn update(&mut self, t_ns: u64, t_tilde: Option<f64>, dom: Option<f64>) -> bool {
        let (tt, d) = match (t_tilde, dom) {
            (Some(a), Some(b)) if a.is_finite() && b.is_finite() => (a, b),
            _ => {
                self.open = false;
                self.calm_since = None;
                return false;
            }
        };
        if self.open {
            if tt > self.p.theta_t || d > self.p.theta_d {
                self.open = false;
                self.calm_since = None;
            }
        } else if tt <= self.p.theta_t - self.p.hyst_t && d <= self.p.theta_d - self.p.hyst_d {
            let since = *self.calm_since.get_or_insert(t_ns);
            if t_ns.saturating_sub(since) >= self.p.hold_ns {
                self.open = true;
            }
        } else {
            self.calm_since = None;
        }
        self.open
    }
}

/// Modulation gπ (Éq. 12) — monotone non croissante, = 1 en régime calme,
/// bornée par g_min. Réutilisée pour le seuil adaptatif ddd_min_t.
#[derive(Debug, Clone, Copy)]
pub struct ModParams {
    pub t0: f64,
    pub a_t: f64,
    pub d0: f64,
    pub a_d: f64,
    pub g_min: f64,
}

pub fn modulation(t_tilde: f64, dom: f64, p: &ModParams) -> f64 {
    let e = -p.a_t * (t_tilde - p.t0).max(0.0) - p.a_d * (dom - p.d0).max(0.0);
    e.exp().clamp(p.g_min, 1.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params() -> GateParams {
        GateParams {
            theta_t: 2.0,
            hyst_t: 0.5,
            theta_d: 0.6,
            hyst_d: 0.05,
            hold_ns: 1_000,
        }
    }

    /// État initial fermé.
    #[test]
    fn gate_starts_closed() {
        let g = Gate::new(params());
        assert!(!g.is_open());
    }

    /// Réouverture seulement après τ_hold de calme strict.
    #[test]
    fn gate_reopens_after_hold_time() {
        let mut g = Gate::new(params());
        // Calme (T̃ ≤ 1.5, D ≤ 0.55) pendant < τ_hold → reste fermé
        assert!(!g.update(0, Some(1.0), Some(0.4)));
        assert!(!g.update(500, Some(1.0), Some(0.4)));
        // τ_hold atteint → ouvert
        assert!(g.update(1_500, Some(1.0), Some(0.4)));
        assert!(g.is_open());
    }

    /// Fermeture immédiate sur dépassement (même juste après ouverture).
    #[test]
    fn gate_closes_immediately_on_breach() {
        let mut g = Gate::new(params());
        g.update(0, Some(1.0), Some(0.4));
        g.update(1_500, Some(1.0), Some(0.4)); // ouvert
        assert!(g.is_open());
        assert!(!g.update(2_000, Some(3.0), Some(0.4))); // T̃ > θ_T
        assert!(!g.is_open());
        // D seul suffit aussi
        let mut g2 = Gate::new(params());
        g2.update(0, Some(1.0), Some(0.4));
        g2.update(1_500, Some(1.0), Some(0.4));
        assert!(!g2.update(2_000, Some(1.0), Some(0.9))); // D > θ_D
    }

    /// Bande δ : entre θ−δ et θ → la porte fermée ne se rouvre pas.
    #[test]
    fn gate_hysteresis_band_blocks_reopen() {
        let mut g = Gate::new(params());
        // T̃ = 1.8 < θ_T=2.0 mais > θ_T−δ=1.5 → pas calme strict → reste fermé
        for k in 0..10 {
            assert!(!g.update(k * 1_000, Some(1.8), Some(0.4)), "k={k}");
        }
    }

    /// Interruption du calme : le timer repart à zéro.
    #[test]
    fn gate_calm_timer_resets_on_disturbance() {
        let mut g = Gate::new(params());
        g.update(0, Some(1.0), Some(0.4)); // calme depuis t=0
        g.update(500, Some(5.0), Some(0.4)); // perturbation (fermé de toute façon)
        // T̃ revient calme à t=600 : nouveau début de calme
        g.update(600, Some(1.0), Some(0.4));
        assert!(!g.update(1_500, Some(1.0), Some(0.4)), "hold relancé à 600");
        assert!(g.update(1_700, Some(1.0), Some(0.4)), "600+1000=1600 ≤ 1700");
    }

    /// Indisponible ⇒ fermé (fail-closed, E3).
    #[test]
    fn gate_fail_closed_on_unavailable() {
        let mut g = Gate::new(params());
        g.update(0, Some(1.0), Some(0.4));
        g.update(1_500, Some(1.0), Some(0.4));
        assert!(g.is_open());
        assert!(!g.update(2_000, None, Some(0.4)));
        assert!(!g.update(3_000, Some(1.0), None));
        assert!(!g.update(4_000, Some(f64::NAN), Some(0.4)));
    }

    /// Anti-battement : en zone grise persistante, au plus ⌈Δ/τ_hold⌉ réouvertures.
    #[test]
    fn gate_bounded_flapping() {
        let mut g = Gate::new(params());
        let mut reopens = 0;
        let mut was_open = false;
        // Alternance calme/bruit toutes les 2 s > τ_hold : chaque cycle peut rouvrir 1×
        for k in 0..20 {
            let (tt, dd) = if k % 2 == 0 { (1.0, 0.4) } else { (5.0, 0.9) };
            let open = g.update(k * 2_000, Some(tt), Some(dd));
            if open && !was_open {
                reopens += 1;
            }
            was_open = open;
        }
        assert!(reopens <= (40_000u64 / 1_000) as usize + 1, "reopens={reopens}");
    }

    // --- modulation gπ (Éq. 12) ---

    /// Régime calme ⇒ g = 1.
    #[test]
    fn modulation_is_one_when_calm() {
        let p = ModParams { t0: 1.0, a_t: 0.5, d0: 0.5, a_d: 2.0, g_min: 0.2 };
        assert_eq!(modulation(0.5, 0.3, &p), 1.0);
        assert_eq!(modulation(1.0, 0.5, &p), 1.0); // aux seuils : (·)+ = 0
    }

    /// g monotone non croissante en chaque argument.
    #[test]
    fn modulation_monotone_nonincreasing() {
        let p = ModParams { t0: 1.0, a_t: 0.5, d0: 0.5, a_d: 2.0, g_min: 0.05 };
        let mut prev = 1.0;
        for k in 0..20 {
            let g = modulation(1.0 + k as f64 * 0.5, 0.5, &p);
            assert!(g <= prev + 1e-12, "non monotone en T̃ à k={k}");
            prev = g;
        }
        prev = 1.0;
        for k in 0..20 {
            let g = modulation(0.0, 0.5 + k as f64 * 0.03, &p);
            assert!(g <= prev + 1e-12, "non monotone en D à k={k}");
            prev = g;
        }
    }

    /// g bornée par g_min (jamais zéro — la modulation ne ferme pas).
    #[test]
    fn modulation_bounded_below_by_gmin() {
        let p = ModParams { t0: 0.0, a_t: 10.0, d0: 0.0, a_d: 10.0, g_min: 0.3 };
        assert_eq!(modulation(100.0, 1.0, &p), 0.3);
    }

    /// Valeur exacte : exp(−a_T(T̃−t0)+ − a_D(D−d0)+).
    #[test]
    fn modulation_exact_formula() {
        let p = ModParams { t0: 1.0, a_t: 0.5, d0: 0.5, a_d: 2.0, g_min: 0.01 };
        let got = modulation(3.0, 0.8, &p);
        let want = (-0.5f64 * (3.0 - 1.0) - 2.0 * (0.8 - 0.5)).exp();
        assert!((got - want).abs() < 1e-12, "{got} vs {want}");
    }
}
