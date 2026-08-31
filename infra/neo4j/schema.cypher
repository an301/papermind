// Constraints and indexes for the PaperMind knowledge graph.
// Applied by scripts/init_neo4j.py against a fresh container.
//
// Intended shape:
//   (:Concept {id, name, aliases})
//   (:Concept)-[:REQUIRES]->(:Concept)          // prerequisite edges, DAG
//   (:User {id})-[:KNOWS {confidence, source, updated_at}]->(:Concept)
//
// TODO: constraints + the 350-concept seed load.
