# Reference Papers

Related research for the WhatsApp misinformation verification bot: users forward rumours (text, image, audio, video), and the bot checks them against earlier fact-checks and live search results, then replies with a verdict and confidence.

Compiled 2026-10-01 from arXiv and ACL Anthology searches. Only titles and search summaries were reviewed, not the full papers, so read each one before citing it.

## 1. Claim matching and the cache (exact + semantic cache)

- [Multilingual Previously Fact-Checked Claim Retrieval](https://aclanthology.org/2023.emnlp-main.1027/) (EMNLP 2023, [arXiv](https://arxiv.org/abs/2305.07991)). Introduces MultiClaim: 28k social posts in 27 languages matched to 206k fact-checks. Closest match to the semantic cache.
- [Claim Matching Beyond English to Scale Global Fact-Checking](https://arxiv.org/html/2106.00853). Claim matching across languages.
- [Investigating Language and Retrieval Bias in Multilingual Previously Fact-Checked Claim Detection](https://arxiv.org/abs/2509.25138). Bias when the query and the fact-check are in different languages.
- [UWBa at SemEval-2025 Task 7](https://arxiv.org/html/2508.09517) and [Word2winners at SemEval-2025 Task 7](https://arxiv.org/html/2503.09011). Current approaches to multilingual and cross-lingual fact-checked claim retrieval.

## 2. WhatsApp and India context

- [Tiplines to Combat Misinformation on Encrypted Platforms](https://arxiv.org/abs/2106.04726). Case study of the 2019 Indian election tipline on WhatsApp. The most direct precedent for this bot.
- [WhatsApp Tiplines and Multilingual Claims in the 2021 Indian Assembly Elections](https://arxiv.org/abs/2507.16298). Tips in English, Hindi and Telugu.
- [Deciphering Viral Trends in WhatsApp](https://arxiv.org/html/2407.08172v1). Village-level study in rural India.

## 3. Search-grounded verification (T2 tier)

- [Resolving Conflicting Evidence in Automated Fact-Checking](https://arxiv.org/abs/2505.17762). RAG verification degrades when sources disagree in credibility.
- [Context Shapes LLMs Retrieval-Augmented Fact-Checking Effectiveness](https://arxiv.org/html/2602.14044). Evidence order in the prompt matters.
- [Multi-Sourced, Multi-Agent Evidence Retrieval for Fact-Checking](https://arxiv.org/html/2603.00267). More advanced retrieval design.
- [Retrieval Augmented Fact Verification by Synthesizing Contrastive Arguments](https://arxiv.org/html/2406.09815v1). Verification by contrasting arguments.
- [Fact-Checking with Contextual Narratives](https://arxiv.org/html/2504.10166). RAG approach for social media content.

## 4. Claim extraction and check-worthiness (extract/classify step)

- [CLEF-2021 CheckThat! overview](https://arxiv.org/abs/2109.12987). Check-worthy claims, previously fact-checked claims, fake news.
- [CLEF-2025 CheckThat!](https://arxiv.org/html/2503.14828v1). Adds claim normalization and retrieval; relevant to the English-translation step.
- [CLEF-2026 CheckThat!](https://arxiv.org/html/2602.09516v1). Latest edition.
- [Check-worthy Claim Detection across Topics for Automated Fact-checking](https://arxiv.org/pdf/2212.08514). Topic generalization.

## 5. Hindi, Marathi and code-mixed input

- [MMCFND](https://arxiv.org/html/2410.10407). Multimodal, multilingual fake news detection for Indic languages.
- [Factorization of Fact-Checks for Low Resource Indian Languages](https://arxiv.org/abs/2102.11276). Introduces the FactDRIL dataset (Hindi, Marathi and others).
- [Better To Ask in English? Evaluating Factual Accuracy of Multilingual LLMs](https://arxiv.org/html/2504.20022v2). English versus low-resource language accuracy. Bears on the bilingual search in `app/providers/tavily.py`.
- [L3Cube-MahaNLP](https://ar5iv.labs.arxiv.org/html/2205.14728). Marathi NLP resources.

## 6. Image claims and memes

- [SNIFFER](https://arxiv.org/html/2403.03170v1). Multimodal LLM for explainable out-of-context misinformation detection.
- [Similarity over Factuality](https://arxiv.org/html/2407.13488v1). Argues progress on out-of-context detection is overstated.
- [E2LVLM](https://arxiv.org/html/2502.10455). Evidence-enhanced vision-language model.
- [Beyond Retrieval: Improving Evidence Quality for LLM-based Multimodal Fact-Checking](https://arxiv.org/abs/2505.03135). Evidence quality for image claims.

## Gaps not yet searched

- Speech recognition for Hindi and Marathi
- Prompt-injection defences for user-forwarded content (Prompt Guard step)
- LLM confidence calibration
