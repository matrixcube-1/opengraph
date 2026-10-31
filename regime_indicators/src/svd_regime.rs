//! Dominance spectrale D et devise dominante c⋆ — conception §3.
//!
//! y_t = p̂_t − p̂_{t−1} (rendements panier, 1ᵀy = 0), fenêtre W.
//! SVD de Y centrée en temps : D = σ₁²/Σσₖ² ;
//! c⋆ = argmax u₁²_i si max ≥ q_min, sinon ∅.
//!
//! Production : gramien glissant G = Gbrut − SSᵀ/W, mises à jour rang-1,
//! recalcul exact tous les K échantillons. Référence : SVD directe.

use nalgebra::{DMatrix, SMatrix, SVector};

pub type Mat8 = SMatrix<f64, 8, 8>;
pub type Vec8 = SVector<f64, 8>;

#[derive(Debug, Clone)]
pub struct SvdRegime {
    /// Valeurs singulières triées décroissant (8).
    pub sigma: [f64; 8],
    /// D = σ₁²/Σσₖ² ∈ [1/rank, 1].
    pub dominance: f64,
    /// c⋆ = argmax u₁² si ≥ q_min, sinon None.
    pub dominant: Option<usize>,
    /// max_i u₁²_i (le critère de c⋆).
    pub loading_sq: f64,
    /// sgn(Σ_τ y_τ,c⋆) sur rendements NON centrés.
    pub direction: i8,
}

/// Construit SvdRegime depuis valeurs singulières triées + u1 + somme brute.
fn build_regime(sigma: [f64; 8], u1: &[f64; 8], raw_sum: &[f64; 8], q_min: f64) -> Option<SvdRegime> {
    let energy: f64 = sigma.iter().map(|x| x * x).sum();
    if !(energy > 0.0) {
        return None;
    }
    let (c, l2) = (0..8)
        .map(|i| (i, u1[i] * u1[i]))
        .fold((0, -1.0), |acc, x| if x.1 > acc.1 { x } else { acc });
    Some(SvdRegime {
        sigma,
        dominance: sigma[0] * sigma[0] / energy,
        dominant: if l2 >= q_min { Some(c) } else { None },
        loading_sq: l2,
        direction: if raw_sum[c] >= 0.0 { 1 } else { -1 },
    })
}

/// SVD directe de référence (audit/tests).
/// `y` = colonnes de rendements bruts p̂-diff (8×W) ; le centrage en temps
/// est fait en interne pour la SVD, la direction utilise les bruts.
pub fn dominance_from_returns(y: &DMatrix<f64>, q_min: f64) -> Option<SvdRegime> {
    if y.iter().any(|v| !v.is_finite()) || y.nrows() != 8 || y.ncols() < 2 {
        return None;
    }
    // Centrage en temps : Y = [y_i − ȳ]
    let mean = y.column_mean();
    let centered = DMatrix::<f64>::from_fn(8, y.ncols(), |i, t| y[(i, t)] - mean[i]);
    let svd = centered.svd(true, false);
    let (u, s) = (svd.u.as_ref()?, &svd.singular_values);
    // Tri explicite + départage par indice : déterminisme indépendant de la bibliothèque.
    let mut idx: Vec<usize> = (0..s.len()).collect();
    idx.sort_by(|&a, &b| s[b].partial_cmp(&s[a]).unwrap().then(a.cmp(&b)));
    let mut sigma = [0.0; 8];
    for (k, &i) in idx.iter().take(8).enumerate() {
        sigma[k] = s[i];
    }
    let u1 = u.column(idx[0]);
    let u1a: [f64; 8] = (0..8)
        .map(|i| u1[i])
        .collect::<Vec<_>>()
        .try_into()
        .unwrap();
    let mut raw_sum = [0.0; 8];
    for t in 0..y.ncols() {
        for i in 0..8 {
            raw_sum[i] += y[(i, t)];
        }
    }
    build_regime(sigma, &u1a, &raw_sum, q_min)
}

/// Gramien glissant : maintient Gbrut = Σyyᵀ et S = Σy sur fenêtre W.
pub struct RollingGram {
    pub w: usize,
    gbrut: Mat8,
    s: Vec8,
    buf: std::collections::VecDeque<Vec8>,
    since_exact: usize,
    /// Recalcul exact du gramien tous les K échantillons (déterministe).
    pub recompute_every: usize,
}

impl RollingGram {
    pub fn new(w: usize, recompute_every: usize) -> Self {
        Self {
            w,
            gbrut: Mat8::zeros(),
            s: Vec8::zeros(),
            buf: std::collections::VecDeque::new(),
            since_exact: 0,
            recompute_every: recompute_every.max(1),
        }
    }

    /// Pousse un échantillon ; retire le plus ancien si fenêtre pleine.
    /// Recalcul exact de Gbrut/S tous les K échantillons (déterministe).
    pub fn push(&mut self, y: &Vec8) {
        if self.buf.len() == self.w {
            let old = self.buf.pop_front().unwrap();
            self.gbrut -= old * old.transpose();
            self.s -= old;
        }
        self.gbrut += y * y.transpose();
        self.s += y;
        self.buf.push_back(*y);
        self.since_exact += 1;
        if self.since_exact >= self.recompute_every && !self.buf.is_empty() {
            // Recalcul exact à instants déterministes (E2, borne la dérive).
            self.gbrut = self
                .buf
                .iter()
                .fold(Mat8::zeros(), |acc, v| acc + v * v.transpose());
            self.s = self.buf.iter().fold(Vec8::zeros(), |acc, v| acc + v);
            self.since_exact = 0;
        }
    }

    /// Gramien centré G = Gbrut − SSᵀ/W (None si fenêtre non pleine).
    pub fn gram(&self) -> Option<Mat8> {
        if self.buf.len() < self.w {
            return None;
        }
        Some(self.gbrut - self.s * self.s.transpose() / self.w as f64)
    }

    /// Dominance par SymmetricEigen du gramien centré.
    /// `q_min` ∈ [1/8, 7/8] ; S (somme non centrée) sert à la direction.
    pub fn dominance(&self, q_min: f64) -> Option<SvdRegime> {
        let g = self.gram()?;
        if !g.iter().all(|v| v.is_finite()) {
            return None;
        }
        // λk(G) = σk²(Y). SymmetricEigen sur matrice symétrique PSD.
        let eig = g.symmetric_eigen();
        let mut idx: Vec<usize> = (0..8).collect();
        idx.sort_by(|&a, &b| {
            eig.eigenvalues[b]
                .partial_cmp(&eig.eigenvalues[a])
                .unwrap()
                .then(a.cmp(&b))
        });
        let mut sigma = [0.0; 8];
        for (k, &i) in idx.iter().enumerate() {
            sigma[k] = eig.eigenvalues[i].max(0.0).sqrt();
        }
        let u1v: Vec8 = eig.eigenvectors.column(idx[0]).into_owned();
        let u1a: [f64; 8] = u1v.as_slice().try_into().unwrap();
        let raw_sum: [f64; 8] = self.s.as_slice().try_into().unwrap();
        build_regime(sigma, &u1a, &raw_sum, q_min)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Indices devises : EUR GBP JPY USD AUD CAD CHF NZD — USD = 3.
    const USD: usize = 3;

    /// Choc USD pur : y_τ = δ_τ·(e_USD − 1/n) — Prop. 3, cas FOMC.
    fn usd_shock() -> DMatrix<f64> {
        let deltas = [0.01, -0.02, 0.015, -0.005, 0.03, -0.01, 0.02, -0.025, 0.008, -0.012];
        let w = deltas.len();
        let mut y = DMatrix::<f64>::zeros(8, w);
        for (t, &d) in deltas.iter().enumerate() {
            for i in 0..8 {
                y[(i, t)] = d * (if i == USD { 1.0 - 1.0 / 8.0 } else { -1.0 / 8.0 });
            }
        }
        y
    }

    /// Bruit diffus déterministe (somme nulle par colonne).
    fn diffuse() -> DMatrix<f64> {
        let w = 40;
        DMatrix::<f64>::from_fn(8, w, |i, t| {
            let v = (((i * 7 + t * 13 + 5) % 11) as f64 - 5.0) * 0.001;
            v
        }) - DMatrix::<f64>::from_fn(8, w, |i, t| {
            // retirer la moyenne de colonne pour garder 1ᵀy = 0
            let col_mean: f64 = (0..8)
                .map(|k| (((k * 7 + t * 13 + 5) % 11) as f64 - 5.0) * 0.001)
                .sum::<f64>()
                / 8.0;
            let _ = i;
            col_mean
        })
    }

    /// Choc USD pur : D = 1, u²₁,USD = 7/8, σ8 = 0.
    #[test]
    fn pure_usd_shock_gives_full_dominance() {
        let r = dominance_from_returns(&usd_shock(), 0.5).unwrap();
        assert!((r.dominance - 1.0).abs() < 1e-9, "D = {}", r.dominance);
        assert_eq!(r.dominant, Some(USD));
        assert!((r.loading_sq - 7.0 / 8.0).abs() < 1e-9, "u²₁,USD = {}", r.loading_sq);
        assert!(r.sigma[7].abs() < 1e-12, "σ8 = {}", r.sigma[7]);
        // Autres devises à 1/56 — vérifié via σ : rang 1 → σ2..7 ≈ 0
        for k in 1..8 {
            assert!(r.sigma[k].abs() < 1e-9, "σ{} = {}", k + 1, r.sigma[k]);
        }
    }

    /// Bruit diffus : D < 1/2 et D ≥ 1/7 (Prop. 3(ii)).
    #[test]
    fn diffuse_noise_low_dominance() {
        let r = dominance_from_returns(&diffuse(), 0.5).unwrap();
        assert!(r.dominance < 0.5, "D = {}", r.dominance);
        assert!(r.dominance >= 1.0 / 7.0 - 1e-9, "D = {}", r.dominance);
        // charges dispersées → pas de devise dominante nette
        assert!(r.dominant.is_none() || r.loading_sq < 0.5);
    }

    /// Gramien glissant = SVD directe à ~1e-9 (voie production vs référence).
    #[test]
    fn rolling_gram_matches_direct_svd() {
        let y = diffuse();
        let w = 30;
        let mut rg = RollingGram::new(w, usize::MAX);
        let mut last = None;
        for t in 0..y.ncols() {
            rg.push(&Vec8::from_fn(|i, _| y[(i, t)]));
            if t >= w - 1 {
                last = Some(t);
            }
        }
        let t = last.unwrap();
        let window = y.columns(t + 1 - w, w).into_owned();
        let direct = dominance_from_returns(&window, 0.5).unwrap();
        let rolling = rg.dominance(0.5).unwrap();
        for k in 0..8 {
            assert!(
                (rolling.sigma[k] - direct.sigma[k]).abs() < 1e-9,
                "σ{} : {} vs {}",
                k + 1,
                rolling.sigma[k],
                direct.sigma[k]
            );
        }
        assert!((rolling.dominance - direct.dominance).abs() < 1e-9);
        assert_eq!(rolling.dominant, direct.dominant);
    }

    /// Fenêtre non pleine → indisponible (fail-closed E3).
    #[test]
    fn rolling_gram_unavailable_until_full() {
        let mut rg = RollingGram::new(10, usize::MAX);
        rg.push(&Vec8::from_fn(|i, _| i as f64 * 0.001 - 0.0035));
        assert!(rg.dominance(0.5).is_none());
        assert!(rg.gram().is_none());
    }

    /// R antisymétrique → valeurs singulières appariées, D(R) ≤ 1/2.
    #[test]
    fn antisymmetric_r_has_paired_singular_values() {
        // R antisymétrique aléatoire déterministe
        let mut r = Mat8::zeros();
        for i in 0..8 {
            for j in (i + 1)..8 {
                let v = ((i * 11 + j * 3 + 1) % 7) as f64 * 0.001 - 0.003;
                r[(i, j)] = v;
                r[(j, i)] = -v;
            }
        }
        let dm = DMatrix::<f64>::from_fn(8, 8, |i, j| r[(i, j)]);
        let svd = dm.svd(false, false);
        let s: Vec<f64> = svd.singular_values.iter().cloned().collect();
        // Paires (σ1≈σ2, σ3≈σ4, ...)
        for k in (0..7).step_by(2) {
            assert!((s[k] - s[k + 1]).abs() < 1e-9, "σ{}≠σ{}: {} vs {}", k + 1, k + 2, s[k], s[k + 1]);
        }
        let e: f64 = s.iter().map(|x| x * x).sum();
        let d = s[0] * s[0] / e;
        assert!(d <= 0.5 + 1e-9, "D(R) = {d} doit être ≤ 1/2");
    }
}
