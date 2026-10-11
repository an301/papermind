import pytest

from papermind.graph.normalize import ConceptIndex, IndexEntry, normalize_concept


@pytest.mark.parametrize(
    "variant",
    [
        "self-attention",
        "Self Attention",
        "self_attention",
        "SELF-ATTENTION",
        "  self   attention ",
        "Self–Attention",
    ],
)
def test_hyphens_underscores_whitespace_and_case_unify(variant):
    assert normalize_concept(variant) == "self attention"


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Transformers", "transformer"),
        ("neural networks", "neural network"),
        ("probabilities", "probability"),
        ("softmaxes", "softmax"),
        ("batches", "batch"),
        ("losses", "loss"),
        ("classes", "class"),
        ("biases", "bias"),
        ("analyses", "analysis"),
        ("hypotheses", "hypothesis"),
        ("local minima", "local minimum"),
        ("matrices", "matrix"),
        ("gammas", "gamma"),
        ("LLMs", "llm"),
        ("GPUs", "gpu"),
        ("ReLUs", "relu"),
    ],
)
def test_plurals_fold_to_singular(raw, key):
    assert normalize_concept(raw) == key


@pytest.mark.parametrize(
    "word",
    [
        "loss",
        "bias",
        "analysis",
        "corpus",
        "process",
        "series",
        "axis",
        "focus",
        "gas",
        "naive bayes",
        "word2vec",
    ],
)
def test_words_ending_in_s_that_are_not_plurals_survive(word):
    assert normalize_concept(word) == word


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Bayes' theorem", "bayes theorem"),
        ("Bayes's theorem", "bayes theorem"),
        ("“Dot-Product”", "dot product"),
        ("Adam (optimizer)", "adam optimizer"),
        ("k-NN", "k nn"),
        ("Fréchet Inception Distance", "frechet inception distance"),
        ("batch normalisation", "batch normalization"),
        ("Optimisers", "optimizer"),
        ("", ""),
        ("--", ""),
    ],
)
def test_punctuation_quotes_accents_and_spelling(raw, key):
    assert normalize_concept(raw) == key


@pytest.fixture
def index() -> ConceptIndex:
    return ConceptIndex(
        [
            IndexEntry("self-attention", "Self-Attention", ("intra-attention",)),
            IndexEntry("batch-normalization", "Batch Normalization", ("batchnorm", "BN")),
            IndexEntry("softmax", "Softmax", ("softmax function",)),
            IndexEntry("attention-mechanism", "Attention Mechanism", ()),
            IndexEntry("adam", "Adam", ()),
            IndexEntry("adamw", "AdamW", ()),
            IndexEntry("relu", "ReLU", ("rectified linear unit",)),
            IndexEntry("gelu", "GELU", ()),
        ]
    )


@pytest.mark.parametrize("query", ["self-attention", "Self Attention", "self_attention", "Intra Attention"])
def test_exact_match_on_any_surface_form(index, query):
    ref, score, suggestions = index.resolve(query)
    assert ref is not None and ref.concept_id == "self-attention"
    assert score == 1.0
    assert suggestions == []


@pytest.mark.parametrize(
    "query", ["batchnorm", "BatchNorm", "batch norm", "batch-norm", "BN", "batch normalisation"]
)
def test_batchnorm_alias_and_spacing(index, query):
    ref, score, _ = index.resolve(query)
    assert ref is not None and ref.concept_id == "batch-normalization"
    assert score >= index.COMPACT_SCORE


def test_fuzzy_accepts_a_typo(index):
    ref, score, _ = index.resolve("softmx")
    assert ref is not None and ref.concept_id == "softmax"
    assert index.FUZZY_ACCEPT / 100 <= score < 1.0


@pytest.mark.parametrize("query", ["elu", "adm", "attention", "normalization"])
def test_fuzzy_refuses_near_but_different_concepts(index, query):
    ref, _, _ = index.resolve(query)
    assert ref is None


def test_sibling_concepts_are_not_confused(index):
    assert index.resolve("gelu").ref.concept_id == "gelu"
    assert index.resolve("relu").ref.concept_id == "relu"
    assert index.resolve("adamw").ref.concept_id == "adamw"


def test_unmatched_query_returns_suggestions(index):
    ref, score, suggestions = index.resolve("attention")
    assert ref is None
    assert 0 < score < index.FUZZY_ACCEPT / 100
    ids = [s.concept_id for s in suggestions]
    assert set(ids[:2]) == {"attention-mechanism", "self-attention"}
    assert "batch-normalization" not in ids


def test_garbage_returns_nothing(index):
    ref, _, suggestions = index.resolve("quantum chromodynamics")
    assert ref is None and suggestions == []
    assert index.resolve("").ref is None


def test_collisions_keep_the_first_owner():
    index = ConceptIndex([IndexEntry("a", "Mean", ()), IndexEntry("b", "Means", ())])
    assert index.resolve("mean").ref.concept_id == "a"
    assert index.collisions == [("mean", "a", "b")]


def test_search_ranks_literal_matches_before_fuzzy(index):
    ids = [ref.concept_id for ref, _ in index.search("att", 5)]
    assert ids[:2] == ["attention-mechanism", "self-attention"]
    assert [ref.concept_id for ref, _ in index.search("", 3)] == ["adam", "adamw", "attention-mechanism"]
