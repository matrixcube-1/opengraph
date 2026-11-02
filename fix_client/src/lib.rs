//! fix_client — adaptateur FIX 4.4 du moteur opengraph.
//! Chemins ouverts FX ≤ 3 jambes, Convention A, compte NETTED.
//! Détection seule par défaut ; aucun ordre sans mode explicite.

pub mod codec;
pub mod session;
