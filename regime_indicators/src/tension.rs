//! Tension multi-sauts T̃ — conception §2.
//!
//! Chaîne : log-taux mids x (antisym.) → potentiel LS p̂ ((L+J/n)p̂ = b)
//! → résidu R (composante cyclique de Hodge) → adjacence A = |R|/s̄
//! → Ā = D^(-1/2) A D^(-1/2) → Ã = Ā + Ā² + Ā³ → M = D^(1/2) Ã D^(1/2)
//! → T = ‖M‖_F / (L·n).

use nalgebra::SMatrix;

pub type Mat8 = SMatrix<f64, 8, 8>;
pub type Vec8 = nalgebra::SVector<f64, 8>;

const N: f64 = 8.0;

/// Erreurs de calcul de régime (fail-closed en amont).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RegimeError {
    /// Graphe de cotation non connexe : p̂ non identifiable.
    Disconnected,
    /// Entrée non finie.
    NonFinite,
}

/// Connexité du graphe d'arêtes par parcours en largeur.
fn is_connected(edges: &Mat8) -> bool {
    let mut seen = [false; 8];
    // Départ : premier nœud incident.
    let Some(start) = (0..8).find(|&i| (0..8).any(|j| edges[(i, j)] > 0.0)) else {
        return false;
    };
    let mut stack = vec![start];
    seen[start] = true;
    while let Some(i) = stack.pop() {
        for j in 0..8 {
            if edges[(i, j)] > 0.0 && !seen[j] {
                seen[j] = true;
                stack.push(j);
            }
        }
    }
    (0..8)
        .filter(|&i| (0..8).any(|j| edges[(i, j)] > 0.0 || edges[(j, i)] > 0.0))
        .all(|i| seen[i])
}

/// Potentiel des moindres carrés : unique solution de (L + J/n) p̂ = b,
/// `b_i = Σ_j x_ij`. `edges` marque les paires cotées (symétrique).
/// Sur K8, forme close p̂_i = (1/n) Σ_j x_ij ; sinon Cholesky 8×8.
pub fn potential(x: &Mat8, edges: &Mat8) -> Result<Vec8, RegimeError> {
    if !x.iter().all(|v| v.is_finite()) {
        return Err(RegimeError::NonFinite);
    }
    if !is_connected(edges) {
        return Err(RegimeError::Disconnected);
    }
    // b_i = Σ_j x_ij (sur les arêtes cotées)
    let b = Vec8::from_fn(|i, _| {
        (0..8)
            .filter(|&j| edges[(i, j)] > 0.0)
            .map(|j| x[(i, j)])
            .sum::<f64>()
    });
    // L + J/n : L = deg·I − adj, J = 11ᵀ
    let m = Mat8::from_fn(|i, j| {
        let deg: f64 = (0..8).filter(|&k| edges[(i, k)] > 0.0).count() as f64;
        let l = if i == j { deg } else { -edges[(i, j)] };
        l + 1.0 / N
    });
    m.cholesky()
        .map(|c| c.solve(&b))
        .ok_or(RegimeError::NonFinite)
}

/// Résidu antisymétrique : R_ij = x_ij − (p̂_i − p̂_j) sur les arêtes cotées.
pub fn residual(x: &Mat8, edges: &Mat8) -> Result<Mat8, RegimeError> {
    let p = potential(x, edges)?;
    Ok(Mat8::from_fn(|i, j| {
        if edges[(i, j)] > 0.0 {
            x[(i, j)] - (p[i] - p[j])
        } else {
            0.0
        }
    }))
}

/// Adjacence de tension : A_ij = |R_ij| / s̄_ij sur les arêtes, 0 sinon.
pub fn adjacency(r: &Mat8, s_norm: &Mat8, edges: &Mat8) -> Mat8 {
    Mat8::from_fn(|i, j| {
        if edges[(i, j)] > 0.0 && s_norm[(i, j)] > 0.0 {
            r[(i, j)].abs() / s_norm[(i, j)]
        } else {
            0.0
        }
    })
}

/// Ā = D^(-1/2) A D^(-1/2) avec garde : d_i <= eps ⇒ (D^(-1/2))_ii = 0.
/// Retourne (Ā, degrés d).
pub fn normalize(a: &Mat8, eps: f64) -> (Mat8, Vec8) {
    let d = Vec8::from_fn(|i, _| a.row(i).sum());
    let inv_sqrt = Vec8::from_fn(|i, _| if d[i] > eps { 1.0 / d[i].sqrt() } else { 0.0 });
    (
        Mat8::from_fn(|i, j| inv_sqrt[i] * a[(i, j)] * inv_sqrt[j]),
        d,
    )
}

/// Ã = Ā + Ā² + … + Ā^L par Horner (L−1 produits 8×8).
pub fn multi_hop(abar: &Mat8, l: usize) -> Mat8 {
    let id = Mat8::identity();
    let mut acc = id;
    for _ in 1..l {
        acc = id + abar * acc;
    }
    abar * acc
}

/// T = ‖D^(1/2) Ã D^(1/2)‖_F / (L·n) — homogène de degré 1 en A.
pub fn tension_from_adjacency(a: &Mat8, l: usize, eps: f64) -> f64 {
    let (abar, d) = normalize(a, eps);
    let at = multi_hop(&abar, l);
    let sq = Vec8::from_fn(|i, _| if d[i] > eps { d[i].sqrt() } else { 0.0 });
    Mat8::from_fn(|i, j| sq[i] * at[(i, j)] * sq[j]).norm() / (l as f64 * N)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Helpers ---------------------------------------------------------

    /// Marché cohérent : x_ij = v_i − v_j (potentiel exact, R ≡ 0).
    fn coherent_x() -> Mat8 {
        let v = [0.3, -0.1, 4.9, 0.0, -0.4, 0.3, -0.2, -0.6];
        Mat8::from_fn(|i, j| v[i] - v[j])
    }

    /// Graphe complet K8.
    fn k8() -> Mat8 {
        Mat8::from_fn(|i, j| if i != j { 1.0 } else { 0.0 })
    }

    /// Matrice antisymétrique déterministe (x_ji = −x_ij).
    fn antisym_x() -> Mat8 {
        let mut m = Mat8::zeros();
        for i in 0..8 {
            for j in (i + 1)..8 {
                let s = ((i * 13 + j * 7 + 3) % 5) as f64 - 2.0;
                m[(i, j)] = s * 0.001;
                m[(j, i)] = -s * 0.001;
            }
        }
        m
    }

    // --- potentiel / résidu (Prop. 1) --------------------------------

    /// Prop. 1(iv) : marché cohérent ⇒ R = 0.
    #[test]
    fn coherent_market_gives_zero_residual() {
        let r = residual(&coherent_x(), &k8()).unwrap();
        assert!(r.norm() < 1e-12, "R devrait être nul: {}", r.norm());
    }

    /// Prop. 1(i) : sur K8, p̂_i = (1/n) Σ_j x_ij.
    #[test]
    fn potential_is_row_mean_on_complete_graph() {
        let x = antisym_x();
        let p = potential(&x, &k8()).unwrap();
        for i in 0..8 {
            let mean: f64 = x.row(i).iter().sum::<f64>() / 8.0;
            assert!((p[i] - mean).abs() < 1e-12, "p̂[{i}] = {} vs moyenne {}", p[i], mean);
        }
    }

    /// Prop. 1(ii) : R_ij = (1/n) Σ_k c_ijk avec c_ijk = x_ij + x_jk + x_ki.
    #[test]
    fn residual_equals_cycle_average() {
        let x = antisym_x();
        let r = residual(&x, &k8()).unwrap();
        for i in 0..8 {
            for j in 0..8 {
                if i == j {
                    continue;
                }
                let c_avg: f64 = (0..8)
                    .map(|k| x[(i, j)] + x[(j, k)] + x[(k, i)])
                    .sum::<f64>()
                    / 8.0;
                assert!((r[(i, j)] - c_avg).abs() < 1e-12);
            }
        }
    }

    /// R antisymétrique et R·1 = 0 (Prop. 1(iii)).
    #[test]
    fn residual_is_antisymmetric_and_orthogonal_to_ones() {
        let x = antisym_x();
        let r = residual(&x, &k8()).unwrap();
        assert!((r + r.transpose()).norm() < 1e-12, "R non antisymétrique");
        let r1 = r * Vec8::repeat(1.0);
        assert!(r1.norm() < 1e-12, "R1 = {}", r1.norm());
    }

    /// Remarque 1 : venue en étoile (arbre) ⇒ R ≡ 0.
    #[test]
    fn star_venue_has_zero_residual() {
        // Seules les paires {0, j} cotées — arbre, aucun cycle.
        let mut edges = Mat8::zeros();
        for j in 1..8 {
            edges[(0, j)] = 1.0;
            edges[(j, 0)] = 1.0;
        }
        let x = Mat8::from_fn(|i, j| {
            if edges[(i, j)] > 0.0 {
                ((i * 31 + j * 17) % 7) as f64 * 0.001
            } else {
                0.0
            }
        });
        // antisymétriser
        let x = x - x.transpose();
        let r = residual(&x, &edges).unwrap();
        assert!(r.norm() < 1e-12, "arbre ⇒ R nul: {}", r.norm());
    }

    /// Graphe non connexe ⇒ erreur Disconnected (E3).
    #[test]
    fn disconnected_graph_is_unavailable() {
        let mut edges = Mat8::zeros();
        // Deux composantes : {0,1} et {2..7}.
        edges[(0, 1)] = 1.0;
        edges[(1, 0)] = 1.0;
        for i in 2..8 {
            for j in 2..8 {
                if i != j {
                    edges[(i, j)] = 1.0;
                }
            }
        }
        let x = antisym_x();
        assert_eq!(residual(&x, &edges), Err(RegimeError::Disconnected));
        assert_eq!(potential(&x, &edges), Err(RegimeError::Disconnected));
    }

    // --- adjacence / tension (Déf. 2, Lemme 1, Éq. 4, Prop. 2) ---------

    /// Déf. 2 : A = |R|/s̄, nulle hors arêtes.
    #[test]
    fn adjacency_is_abs_residual_over_spread() {
        let mut r = Mat8::zeros();
        r[(0, 1)] = 0.004;
        r[(1, 0)] = -0.004;
        let mut s = Mat8::from_fn(|_, _| 0.002);
        s.fill_diagonal(1.0);
        let edges = Mat8::from_fn(|i, j| if (i, j) == (0, 1) || (i, j) == (1, 0) { 1.0 } else { 0.0 });
        let a = adjacency(&r, &s, &edges);
        assert!((a[(0, 1)] - 2.0).abs() < 1e-12);
        assert!((a[(2, 3)] - 0.0).abs() < 1e-12, "hors arêtes");
    }

    /// Lemme 1 : le spectre de Ā est dans [−1, 1] et contient 1.
    #[test]
    fn normalized_adjacency_spectrum_in_unit_disk() {
        let r = residual(&antisym_x(), &k8()).unwrap();
        let s = Mat8::from_fn(|_, _| 0.002);
        let a = adjacency(&r, &s, &k8());
        let (abar, _d) = normalize(&a, 1e-12);
        let eigs = abar.symmetric_eigenvalues();
        let max_ev = eigs.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let min_ev = eigs.iter().cloned().fold(f64::INFINITY, f64::min);
        assert!((max_ev - 1.0).abs() < 1e-9, "1 doit être valeur propre: {eigs}");
        assert!(min_ev >= -1.0 - 1e-9, "spectre ⊆ [−1,1]: {eigs}");
    }

    /// Lemme 1 : Ā(cA) = Ā(A).
    #[test]
    fn normalized_adjacency_is_scale_invariant() {
        let r = residual(&antisym_x(), &k8()).unwrap();
        let s = Mat8::from_fn(|_, _| 0.002);
        let a = adjacency(&r, &s, &k8());
        let (a1, _) = normalize(&a, 1e-12);
        let (a2, _) = normalize(&(a * 7.3), 1e-12);
        assert!((a1 - a2).norm() < 1e-12, "Ā doit être invariant d'échelle");
    }

    /// Horner = puissances explicites : Ã = Ā + Ā² + Ā³.
    #[test]
    fn multi_hop_matches_explicit_powers() {
        let r = residual(&antisym_x(), &k8()).unwrap();
        let s = Mat8::from_fn(|_, _| 0.002);
        let a = adjacency(&r, &s, &k8());
        let (abar, _) = normalize(&a, 1e-12);
        let horner = multi_hop(&abar, 3);
        let explicit = abar + abar * abar + abar * abar * abar;
        assert!((horner - explicit).norm() < 1e-12);
    }

    /// Éq. (4) : M = A + AD⁺A + AD⁺AD⁺A — vérifier l'identité dé-normalisée.
    #[test]
    fn denormalized_m_matches_a_plus_walk_sums() {
        let r = residual(&antisym_x(), &k8()).unwrap();
        let s = Mat8::from_fn(|_, _| 0.002);
        let a = adjacency(&r, &s, &k8());
        let (abar, d) = normalize(&a, 1e-12);
        let at = multi_hop(&abar, 3);
        // M = D^{1/2} Ã D^{1/2}
        let sq = Vec8::from_fn(|i, _| if d[i] > 1e-12 { d[i].sqrt() } else { 0.0 });
        let m = Mat8::from_fn(|i, j| sq[i] * at[(i, j)] * sq[j]);
        // Référence : A + A·D⁺·A + A·D⁺·A·D⁺·A  (D⁺ diagonale de 1/d)
        let dp = Vec8::from_fn(|i, _| if d[i] > 1e-12 { 1.0 / d[i] } else { 0.0 });
        let ddiag = Mat8::from_diagonal(&dp);
        let m_ref = a + a * ddiag * a + a * ddiag * a * ddiag * a;
        assert!((m - m_ref).norm() < 1e-10, "identité (4): {}", (m - m_ref).norm());
    }

    /// Prop. 2(i) : T = 0 ⇔ A = 0.
    #[test]
    fn zero_adjacency_gives_zero_tension() {
        assert_eq!(tension_from_adjacency(&Mat8::zeros(), 3, 1e-12), 0.0);
    }

    /// Prop. 2(ii) : T(cA) = c·T(A) — homogénéité de degré 1.
    #[test]
    fn tension_is_homogeneous_degree_one() {
        let r = residual(&antisym_x(), &k8()).unwrap();
        let s = Mat8::from_fn(|_, _| 0.002);
        let a = adjacency(&r, &s, &k8());
        let t1 = tension_from_adjacency(&a, 3, 1e-12);
        let t2 = tension_from_adjacency(&(a * 7.3), 3, 1e-12);
        assert!((t2 / t1 - 7.3).abs() < 1e-9, "{t2}/{t1} = {}", t2 / t1);
    }

    /// Prop. 2(iii) : T invariant par permutation des devises.
    #[test]
    fn tension_is_permutation_invariant() {
        let r = residual(&antisym_x(), &k8()).unwrap();
        let s = Mat8::from_fn(|_, _| 0.002);
        let a = adjacency(&r, &s, &k8());
        let perm = [3, 0, 7, 1, 5, 2, 6, 4];
        let ap = Mat8::from_fn(|i, j| a[(perm[i], perm[j])]);
        let t1 = tension_from_adjacency(&a, 3, 1e-12);
        let t2 = tension_from_adjacency(&ap, 3, 1e-12);
        assert!((t1 - t2).abs() < 1e-12, "{t1} vs {t2}");
    }

    /// Garde : degré nul (devise isolée) ne doit pas produire NaN.
    #[test]
    fn zero_degree_guard_no_nan() {
        let mut a = Mat8::zeros();
        a[(0, 1)] = 1.0;
        a[(1, 0)] = 1.0;
        let t = tension_from_adjacency(&a, 3, 1e-12);
        assert!(t.is_finite(), "T doit être fini: {t}");
        assert!(t > 0.0);
    }
}
