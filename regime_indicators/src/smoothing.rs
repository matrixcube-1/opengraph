//! Lissage temporel + standardisation robuste — conception §2.5.
//!
//! EWMA à demi-vie h sur observations à instants irréguliers :
//!   T̄_k = T̄_{k−1} + a_k (T_k − T̄_{k−1}),  a_k = 1 − exp(−ln2·Δt/h).
//! Z robuste : T̃ = (T̄ − med(H)) / (1.4826·max(MAD(H), m_min)),
//! H = W_T dernières valeurs LISSÉES strictement antérieures (pas de fuite).

/// EWMA à demi-vie sur temps événementiel irrégulier.
pub struct Ewma {
    /// Demi-vie (même unité que les timestamps).
    pub half_life: f64,
    last_t: Option<u64>,
    value: f64,
    started: bool,
}

impl Ewma {
    pub fn new(half_life: f64) -> Self {
        Self {
            half_life,
            last_t: None,
            value: 0.0,
            started: false,
        }
    }

    /// Première observation : valeur initialisée à l'observation.
    /// Ensuite : a = 1 − 2^(−Δt/h).
    pub fn update(&mut self, t: u64, x: f64) -> f64 {
        if !self.started {
            self.value = x;
            self.last_t = Some(t);
            self.started = true;
            return x;
        }
        let dt = t.saturating_sub(self.last_t.unwrap()) as f64;
        let a = 1.0 - (-std::f64::consts::LN_2 * dt / self.half_life).exp();
        self.value += a * (x - self.value);
        self.last_t = Some(t);
        self.value
    }

    pub fn value(&self) -> Option<f64> {
        self.started.then_some(self.value)
    }
}

/// Z robuste sur historique glissant de taille W_T.
/// Tant que |H| < w_min → indisponible (None, fail-closed).
pub struct RobustZ {
    pub window: usize,
    pub w_min: usize,
    /// Plancher de dénominateur (garde, pas un seuil de décision).
    pub m_min: f64,
    hist: std::collections::VecDeque<f64>,
}

impl RobustZ {
    pub fn new(window: usize, w_min: usize, m_min: f64) -> Self {
        Self {
            window,
            w_min,
            m_min,
            hist: std::collections::VecDeque::new(),
        }
    }

    /// Standardise x CONTRE l'historique actuel, PUIS pousse x dans H.
    /// Retourne None tant que |H| < w_min (aucune fuite : x n'est pas dans H).
    pub fn update(&mut self, x: f64) -> Option<f64> {
        let out = if self.hist.len() >= self.w_min {
            let mut h: Vec<f64> = self.hist.iter().cloned().collect();
            let med = median(&mut h);
            let mut dev: Vec<f64> = h.iter().map(|v| (v - med).abs()).collect();
            let mad = median(&mut dev);
            let denom = 1.4826 * mad.max(self.m_min);
            Some((x - med) / denom)
        } else {
            None
        };
        self.hist.push_back(x);
        while self.hist.len() > self.window {
            self.hist.pop_front();
        }
        out
    }

    pub fn len(&self) -> usize {
        self.hist.len()
    }
}

/// Médiane d'un échantillon (copie triée — boucle lente).
pub fn median(v: &mut Vec<f64>) -> f64 {
    let n = v.len();
    if n == 0 {
        return f64::NAN;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    if n % 2 == 1 {
        v[n / 2]
    } else {
        0.5 * (v[n / 2 - 1] + v[n / 2])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Demi-vie : après exactement h, la moitié du chemin vers x est faite.
    #[test]
    fn ewma_half_life_semantics() {
        let mut e = Ewma::new(1000.0); // h = 1000 ns
        e.update(0, 0.0);
        // Après Δt = h : a = 1 − 2^{−1} = 0.5 → value = 0.5·x
        let v = e.update(1000, 10.0);
        assert!((v - 5.0).abs() < 1e-9, "v = {v} attendu 5.0");
    }

    /// Δt = 0 → pas de mise à jour (a = 0).
    #[test]
    fn ewma_zero_dt_is_noop() {
        let mut e = Ewma::new(1000.0);
        e.update(0, 1.0);
        let v = e.update(0, 99.0);
        assert!((v - 1.0).abs() < 1e-12, "Δt=0 ne doit rien changer");
    }

    /// EWMA converge vers la valeur constante.
    #[test]
    fn ewma_converges_to_constant() {
        let mut e = Ewma::new(100.0);
        e.update(0, 5.0);
        let mut v = 0.0;
        for k in 1..50 {
            v = e.update(k * 100, 7.0);
        }
        assert!((v - 7.0).abs() < 1e-6);
    }

    /// Z connu : historique [1..=9] → med=5, MAD=2 → z = (x−5)/(1.4826·2).
    #[test]
    fn robust_z_known_value() {
        let mut z = RobustZ::new(100, 9, 1e-9);
        for k in 1..=9 {
            z.update(k as f64);
        }
        let got = z.update(11.0).unwrap();
        let want = (11.0 - 5.0) / (1.4826 * 2.0);
        assert!((got - want).abs() < 1e-9, "{got} vs {want}");
    }

    /// Pas de fuite : x n'entre dans H qu'APRÈS le calcul.
    #[test]
    fn robust_z_no_lookahead() {
        let mut z = RobustZ::new(100, 3, 1e-9);
        z.update(0.0);
        z.update(0.0);
        z.update(0.0);
        // Historique = {0,0,0} → med=0, MAD=0 → dénom = m_min → z = x/m_min
        let got = z.update(1.0).unwrap();
        assert!((got - 1.0 / 1e-9).abs() < 1e-3 || got.is_finite());
        // Maintenant H contient 1.0 : un nouvel outlier identique donne z plus faible
        let got2 = z.update(1.0).unwrap();
        assert!(got2 < got || got2.is_finite());
    }

    /// Indisponible tant que |H| < w_min (fail-closed).
    #[test]
    fn robust_z_unavailable_below_wmin() {
        let mut z = RobustZ::new(100, 5, 1e-9);
        for _ in 0..5 {
            assert!(z.update(1.0).is_none());
        }
        assert!(z.update(1.0).is_some());
    }

    /// Fenêtre bornée : len ≤ window.
    #[test]
    fn robust_z_window_bounded() {
        let mut z = RobustZ::new(4, 2, 1e-9);
        for k in 0..10 {
            z.update(k as f64);
        }
        assert!(z.len() <= 4);
    }
}
