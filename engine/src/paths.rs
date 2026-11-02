//! Énumération des chemins ouverts — DFS sur le graphe des 8 devises.
//!
//! Port fidèle de `poc_engine.enum_paths` (Python). Un chemin = suite de
//! jambes FX (indices d'arêtes de [`crate::universe::FX`]), 1 à 3 jambes,
//! paires bidirectionnelles, sans retour sur un nœud déjà visité.
//!
//! Convention A : les variantes à terminaison CFD ne sont PAS énumérées
//! (dominées sous marquage aux prix exécutables — voir AGENTS.md).

use crate::universe::pair_nodes;

/// Un chemin ouvert : indices d'arêtes FX (longueur 1..=3).
pub type Path = Vec<u8>;

/// Énumère tous les chemins ouverts FX purs.
///
/// Retourne `(paths, starts)` : `starts[i]` = nœud de départ réel du DFS —
/// un chemin peut partir du côté quote de sa première arête, pas
/// nécessairement de la base de la paire (correctif R6 du POC).
///
/// Le résultat attendu est exactement **2 072 chemins**.
pub fn enum_paths() -> (Vec<Path>, Vec<u8>) {
    // adj[node] = (indice d'arête, autre nœud) — paires bidirectionnelles.
    let mut adj: Vec<Vec<(u8, u8)>> = vec![Vec::new(); 8];
    for ei in 0..28u8 {
        let (a, b) = pair_nodes(ei as usize);
        adj[a as usize].push((ei, b));
        adj[b as usize].push((ei, a));
    }

    let mut paths: Vec<Path> = Vec::with_capacity(2072);
    let mut starts: Vec<u8> = Vec::with_capacity(2072);
    for s0 in 0..8u8 {
        dfs(
            s0,
            1u16 << s0,
            &mut Vec::new(),
            s0,
            &adj,
            &mut paths,
            &mut starts,
        );
    }
    (paths, starts)
}

fn dfs(
    node: u8,
    seen: u16,
    eis: &mut Path,
    s0: u8,
    adj: &[Vec<(u8, u8)>],
    paths: &mut Vec<Path>,
    starts: &mut Vec<u8>,
) {
    if !eis.is_empty() {
        paths.push(eis.clone());
        starts.push(s0);
    }
    if eis.len() >= 3 {
        return;
    }
    for &(ei, b) in &adj[node as usize] {
        if seen & (1u16 << b) == 0 {
            eis.push(ei);
            dfs(b, seen | (1u16 << b), eis, s0, adj, paths, starts);
            eis.pop();
        }
    }
}

/// Tables précompilées des chemins — miroir de `_vectorize` du POC.
///
/// Pour chaque chemin `pi` et chaque jambe `li < p_l[pi]` :
/// - `p_e`    : indice d'arête FX (0..28)
/// - `p_dir`  : +1 si le nœud courant est la base (vente au bid), -1 sinon
/// - `p_base` : indice de la devise de base (commission)
pub struct PathTables {
    pub paths: Vec<Path>,
    pub p_s: Vec<u8>,
    pub p_end: Vec<u8>,
    pub p_l: Vec<u8>,
    pub p_e: Vec<[i8; 3]>,
    pub p_dir: Vec<[i8; 3]>,
    pub p_base: Vec<[i8; 3]>,
}

impl PathTables {
    pub fn len(&self) -> usize {
        self.paths.len()
    }

    pub fn is_empty(&self) -> bool {
        self.paths.is_empty()
    }
}

/// Construit les tables par propagation de nœud — identique au POC :
/// `d = +1 si node == base`, sinon `-1` ; `node = autre côté`.
pub fn build_tables() -> PathTables {
    let (paths, starts) = enum_paths();
    let n = paths.len();
    let mut t = PathTables {
        p_s: vec![0; n],
        p_end: vec![0; n],
        p_l: vec![0; n],
        p_e: vec![[-1; 3]; n],
        p_dir: vec![[1; 3]; n],
        p_base: vec![[-1; 3]; n],
        paths,
    };
    for (pi, path) in t.paths.iter().enumerate() {
        let mut node = starts[pi];
        t.p_s[pi] = node;
        for (li, &e) in path.iter().enumerate() {
            let (a, b) = pair_nodes(e as usize);
            // audit OG2-B2 : un nœud hors de l'arête serait exécuté à
            // l'envers (d=-1 silencieux) — on fige l'invariant à la
            // construction des tables plutôt qu'à chaque jambe live.
            assert!(
                node == a || node == b,
                "build_tables : nœud {node} hors arête ({a},{b})"
            );
            let d: i8 = if node == a { 1 } else { -1 };
            t.p_dir[pi][li] = d;
            t.p_e[pi][li] = e as i8;
            t.p_base[pi][li] = a as i8;
            node = if d == 1 { b } else { a };
            t.p_end[pi] = node;
        }
        t.p_l[pi] = path.len() as u8;
    }
    t
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Verrou de régression : le POC Python produit exactement 2 072
    /// chemins FX ouverts sur l'univers Annexe J.
    #[test]
    fn fx_path_count_is_2072() {
        let (paths, starts) = enum_paths();
        assert_eq!(paths.len(), 2072);
        assert_eq!(starts.len(), 2072);
        assert!(paths.iter().all(|p| (1..=3).contains(&p.len())));
    }

    /// Les tables propagent le nœud correctement : chaque jambe part du
    /// nœud où la précédente s'est terminée ; p_s/p_end cohérents.
    #[test]
    fn tables_node_propagation() {
        let t = build_tables();
        assert_eq!(t.len(), 2072);
        for pi in 0..t.len() {
            let mut node = t.p_s[pi];
            for li in 0..t.p_l[pi] as usize {
                let (a, b) = pair_nodes(t.p_e[pi][li] as usize);
                // dir +1 : départ à la base ; dir -1 : départ à la quote
                assert_eq!(node, if t.p_dir[pi][li] == 1 { a } else { b });
                node = if t.p_dir[pi][li] == 1 { b } else { a };
            }
            assert_eq!(node, t.p_end[pi]);
        }
    }

    /// Chemin 1 jambe présent : part des deux côtés de chaque arête.
    #[test]
    fn one_leg_paths_both_sides() {
        let t = build_tables();
        let one = (0..t.len()).filter(|&pi| t.p_l[pi] == 1).count();
        assert_eq!(one, 56); // 28 arêtes x 2 sens
    }
}
