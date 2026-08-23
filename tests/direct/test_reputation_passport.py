import json

PROMPT_REGEX = r"Assess whether this peer review is authentic"

AUTHENTIC_MOCK = {
    "authentic": True,
    "confidence": 92,
    "reasoning": "references a verifiable personal experience",
}

FAKE_MOCK = {
    "authentic": False,
    "confidence": 87,
    "reasoning": "generic incentivized wording, no specifics",
}

LINKS = ["https://example.com/profile"]

GOOD_TEXT = "Alice delivered exactly as promised, quick and professional."


def _deploy(direct_vm, direct_deploy, deployer):
    """Deploy with `deployer` as sender; the deployer becomes the owner."""
    direct_vm.sender = deployer
    return direct_deploy("contracts/ReputationPassport.py")


def _register(direct_vm, contract, who, name):
    with direct_vm.prank(who):
        contract.register_profile(name, LINKS)


def _submit(direct_vm, contract, reviewer, review_id, subject, rating, text=GOOD_TEXT):
    with direct_vm.prank(reviewer):
        contract.submit_review(review_id, subject, rating, text)


def _verify(contract, review_id):
    contract.verify_review(review_id)


def test_register_profiles_and_submit_first_review(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Alice and bob register; bob leaves a pending 5-star review for alice."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")
    _register(direct_vm, contract, direct_bob, "Bob")

    assert len(contract.owner()) > 0

    _submit(direct_vm, contract, direct_bob, "rev-1", direct_alice, 5)

    review = contract.get_review("rev-1")
    assert len(review["subject_key"]) > 0
    assert len(review["reviewer_key"]) > 0
    assert review["rating_x10"] == 50
    assert review["status"] == "pending"
    assert review["confidence"] == 0
    assert contract.total_reviews() == 1


def test_duplicate_profile_registration_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """An address can register only one profile."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")

    with direct_vm.expect_revert("Profile already registered"):
        _register(direct_vm, contract, direct_alice, "Alice Prime")


def test_empty_profile_name_reverts(direct_vm, direct_deploy, direct_alice):
    """A profile display name must not be blank."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)

    with direct_vm.expect_revert("Display name must not be empty"):
        with direct_vm.prank(direct_alice):
            contract.register_profile("   ", LINKS)


def test_authentic_review_updates_running_average(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """A 5-star review judged authentic feeds the O(1) average (50/10 == 5 stars)."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")
    _submit(direct_vm, contract, direct_bob, "rev-1", direct_alice, 5)

    direct_vm.mock_llm(PROMPT_REGEX, json.dumps(AUTHENTIC_MOCK))
    _verify(contract, "rev-1")

    reputation = contract.reputation_of(direct_alice)
    assert reputation["avg_rating_x10"] == 50
    assert reputation["count"] == 1

    review = contract.get_review("rev-1")
    assert review["status"] == "authentic"
    assert review["confidence"] == 92
    assert len(review["reasoning"]) > 0


def test_second_authentic_review_recomputes_average(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """Reviews of 5 and 3 stars yield avg_rating_x10 of (50+30)//2 == 40 with count 2."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")
    _submit(direct_vm, contract, direct_bob, "rev-1", direct_alice, 5)

    direct_vm.mock_llm(PROMPT_REGEX, json.dumps(AUTHENTIC_MOCK))
    _verify(contract, "rev-1")

    _submit(
        direct_vm,
        contract,
        direct_charlie,
        "rev-2",
        direct_alice,
        3,
        "Decent service but slow to respond.",
    )
    direct_vm.mock_llm(PROMPT_REGEX, json.dumps(AUTHENTIC_MOCK))
    _verify(contract, "rev-2")

    reputation = contract.reputation_of(direct_alice)
    assert reputation["avg_rating_x10"] == 40
    assert reputation["count"] == 2
    assert contract.total_reviews() == 2


def test_fake_verdict_leaves_reputation_unchanged(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Only AUTHENTIC reviews feed the average; a fake verdict stores no rating."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")
    _submit(direct_vm, contract, direct_bob, "rev-1", direct_alice, 5)

    direct_vm.mock_llm(PROMPT_REGEX, json.dumps(FAKE_MOCK))
    _verify(contract, "rev-1")

    reputation = contract.reputation_of(direct_alice)
    assert reputation["avg_rating_x10"] == 0
    assert reputation["count"] == 0

    review = contract.get_review("rev-1")
    assert review["status"] == "fake"
    assert review["confidence"] == 87


def test_self_review_reverts(direct_vm, direct_deploy, direct_alice):
    """A profile cannot review itself."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")

    with direct_vm.expect_revert("Cannot review own profile"):
        _submit(direct_vm, contract, direct_alice, "rev-self", direct_alice, 5)


def test_zero_rating_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Ratings below 1 star are rejected."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")

    with direct_vm.expect_revert("Rating must be between 1 and 5"):
        _submit(direct_vm, contract, direct_bob, "rev-zero", direct_alice, 0)


def test_rating_above_five_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Ratings above 5 stars are rejected."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")

    with direct_vm.expect_revert("Rating must be between 1 and 5"):
        _submit(direct_vm, contract, direct_bob, "rev-six", direct_alice, 6)


def test_unknown_subject_profile_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """Reviews may only target addresses that have a registered profile."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")

    with direct_vm.expect_revert("Subject profile not found"):
        _submit(direct_vm, contract, direct_bob, "rev-ghost", direct_charlie, 4)


def test_double_verify_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """A review can only be verified once; verified reviews are no longer pending."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")
    _submit(direct_vm, contract, direct_bob, "rev-1", direct_alice, 5)

    direct_vm.mock_llm(PROMPT_REGEX, json.dumps(AUTHENTIC_MOCK))
    _verify(contract, "rev-1")

    with direct_vm.expect_revert("Review is not pending"):
        _verify(contract, "rev-1")

    with direct_vm.expect_revert("Unknown review id"):
        contract.get_review("missing-review")


def test_unparseable_llm_verdict_raises_llm_error(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """An LLM answer with an invalid bool word and no authenticity key fails as [LLM_ERROR]."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _register(direct_vm, contract, direct_alice, "Alice")
    _submit(direct_vm, contract, direct_bob, "rev-1", direct_alice, 5)

    direct_vm.mock_llm(
        PROMPT_REGEX,
        json.dumps({"result": "maybe", "confidence": 55, "reasoning": "not sure"}),
    )
    with direct_vm.expect_revert("[LLM_ERROR]"):
        _verify(contract, "rev-1")
