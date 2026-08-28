# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import json


ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"

STATUS_PENDING = "pending"
STATUS_AUTHENTIC = "authentic"
STATUS_FAKE = "fake"


def _parse_llm_json(text) -> dict:
	import re
	if isinstance(text, dict):
		return text
	s = str(text)
	first = s.find("{")
	last = s.rfind("}")
	if first == -1 or last <= first:
		raise gl.vm.UserError(f"{ERROR_LLM} no JSON object found in LLM output")
	s = s[first : last + 1]
	s = re.sub(r",(?!\s*?[\{\[\"\'\w])", "", s)
	try:
		parsed = json.loads(s)
	except Exception:
		raise gl.vm.UserError(f"{ERROR_LLM} malformed JSON from LLM")
	if not isinstance(parsed, dict):
		raise gl.vm.UserError(f"{ERROR_LLM} non-dict JSON from LLM")
	return parsed


def _coerce_bool(raw) -> bool:
	if isinstance(raw, bool):
		return raw
	s = str(raw).strip().lower()
	if s in ("true", "1", "yes"):
		return True
	if s in ("false", "0", "no"):
		return False
	raise gl.vm.UserError(f"{ERROR_LLM} non-boolean authenticity field in LLM output")


def _handle_leader_error(leaders_res, leader_fn) -> bool:
	leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
	try:
		leader_fn()
		return False
	except gl.vm.UserError as e:
		validator_msg = e.message if hasattr(e, "message") else str(e)
		if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
			return validator_msg == leader_msg
		if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
			return True
		return False
	except Exception:
		return False


@allow_storage
@dataclass
class Profile:
	display_name: str
	links: DynArray[str]


@allow_storage
@dataclass
class Review:
	subject_key: str
	reviewer_key: str
	rating_x10: u256
	text: str
	status: str
	confidence: u256
	reasoning: str


class ReputationPassport(gl.Contract):
	owner_addr: Address
	profiles: TreeMap[str, Profile]
	reviews: TreeMap[str, Review]
	sum_rating_x10: TreeMap[str, u256]
	review_count: TreeMap[str, u256]
	review_ids: DynArray[str]

	def __init__(self) -> None:
		self.owner_addr = gl.message.sender_address

	def _get_review(self, review_id: str) -> Review:
		review = self.reviews.get(review_id)
		if review is None:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Unknown review id")
		return review

	@gl.public.view
	def owner(self) -> str:
		return str(self.owner_addr)

	@gl.public.write
	def register_profile(self, name: str, links: DynArray[str]) -> None:
		if len(name.strip()) == 0:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Display name must not be empty")
		sender_key = str(gl.message.sender_address)
		if sender_key in self.profiles:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Profile already registered")
		self.profiles[sender_key] = Profile(display_name=name, links=[])
		bucket = self.profiles[sender_key].links
		for i in range(len(links)):
			bucket.append(str(links[i]))

	@gl.public.write
	def submit_review(
		self, review_id: str, subject: Address, rating: u256, text: str
	) -> None:
		subject_addr = Address(subject)
		subject_key = str(subject_addr)
		clean_id = str(review_id).strip()
		clean_text = str(text).strip()
		if not clean_id or not clean_text:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Review id and text must not be empty")
		if subject_key not in self.profiles:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Subject profile not found")
		if rating < u256(1) or rating > u256(5):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Rating must be between 1 and 5")
		reviewer_key = str(gl.message.sender_address)
		if reviewer_key == subject_key:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Cannot review own profile")
		if clean_id in self.reviews:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Review id already exists")
		self.reviews[clean_id] = Review(
			subject_key=subject_key,
			reviewer_key=reviewer_key,
			rating_x10=u256(rating * u256(10)),
			text=clean_text,
			status=STATUS_PENDING,
			confidence=u256(0),
			reasoning="",
		)
		self.review_ids.append(clean_id)

	@gl.public.write
	def verify_review(self, review_id: str) -> None:
		review = self._get_review(review_id)
		if review.status != STATUS_PENDING:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Review is not pending")

		def leader_fn() -> dict:
			r = self.reviews.get(review_id)
			out = gl.nondet.exec_prompt(
				"Assess whether this peer review is authentic or fake/incentivized.\n"
				"REVIEW TEXT: <review>" + r.text + "</review>\n"
				"RATING: " + str(r.rating_x10) + "/50\n"
				'Reply JSON {"authentic": true/false, "confidence": 0-100, '
				'"reasoning": "..."}',
				response_format="json",
			)
			parsed = _parse_llm_json(out)
			raw = None
			for key in ("authentic", "is_authentic", "genuine"):
				if key in parsed:
					raw = parsed[key]
					break
			if raw is None:
				raise gl.vm.UserError(
					f"{ERROR_LLM} missing authenticity field in LLM output"
				)
			authentic = _coerce_bool(raw)
			raw_confidence = parsed.get("confidence", 0)
			try:
				confidence = int(float(raw_confidence))
			except Exception:
				confidence = 0
			if confidence < 0:
				confidence = 0
			if confidence > 100:
				confidence = 100
			return {
				"authentic": bool(authentic),
				"confidence": int(confidence),
				"reasoning": str(parsed.get("reasoning", "")),
				"subject_key": r.subject_key,
				"rating_x10": int(r.rating_x10),
			}

		def validator_fn(leaders_res: gl.vm.Result) -> bool:
			if not isinstance(leaders_res, gl.vm.Return):
				return _handle_leader_error(leaders_res, leader_fn)
			leader_data = leaders_res.calldata
			fresh = leader_fn()
			return bool(leader_data.get("authentic")) == bool(fresh.get("authentic"))

		result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

		authentic = bool(result["authentic"])
		key = str(review.subject_key)
		if authentic:
			self.sum_rating_x10[key] = (
				self.sum_rating_x10.get(key, u256(0)) + review.rating_x10
			)
			self.review_count[key] = self.review_count.get(key, u256(0)) + u256(1)
			review.status = STATUS_AUTHENTIC
		else:
			review.status = STATUS_FAKE
		review.confidence = u256(int(result["confidence"]))
		review.reasoning = str(result["reasoning"])

	@gl.public.view
	def reputation_of(self, who: Address) -> dict:
		who_addr = Address(who)
		key = str(who_addr)
		count = self.review_count.get(key, u256(0))
		total = self.sum_rating_x10.get(key, u256(0))
		avg = total // count if count > u256(0) else u256(0)
		return {"avg_rating_x10": u256(avg), "count": u256(count)}

	@gl.public.view
	def get_review(self, review_id: str) -> dict:
		review = self._get_review(review_id)
		return {
			"subject_key": review.subject_key,
			"reviewer_key": review.reviewer_key,
			"rating_x10": review.rating_x10,
			"text": review.text,
			"status": review.status,
			"confidence": review.confidence,
			"reasoning": review.reasoning,
		}

	@gl.public.view
	def total_reviews(self) -> u256:
		return u256(len(self.review_ids))
