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
        dfs(s0, 1u16 << s0, &mut Vec::new(), s0, &adj, &mut paths, &mut starts);
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
}
