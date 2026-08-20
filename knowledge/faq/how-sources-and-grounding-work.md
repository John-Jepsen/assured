---
title: How Sources and Grounding Work
category: faq
---

# How Sources and Grounding Work

When the assistant answers an educational question, it does not rely on memory alone. It searches a curated knowledge base of insurance articles and pulls the passages most relevant to your question, then bases its answer on those passages. This approach is called grounding or retrieval-augmented generation, and it keeps answers tied to reviewed content rather than to whatever the model might otherwise produce. The result is that responses reflect the knowledge base you can inspect, not invented facts.

The knowledge base is built from markdown articles that are split into small, self-contained chunks so that each piece can be matched independently. When you ask a question, the system converts both your question and the stored chunks into numerical representations called embeddings and compares them to find the closest matches by meaning. It selects a small number of the highest-scoring passages, typically the top few, and only uses passages that clear a minimum relevance threshold. Chunks that are too weakly related are left out so they do not pollute the answer.

Because answers are grounded, the assistant can attribute them to their sources. When it uses retrieved passages, it points to the articles those passages came from, so you can see where the information originated and read more if you want. Source attribution builds trust and makes it easy to check an answer. It also distinguishes grounded educational content from account-specific facts, which come from verified records rather than from the knowledge base.

A key safety behavior is that the assistant will not fill gaps with guesses. If the retrieval step does not find passages that clear the relevance threshold, the assistant tells you honestly that it does not have enough information to answer, and it offers to escalate to a human. It never invents coverage, exclusions, limits, or policy terms to appear more helpful. Admitting a gap is treated as the correct outcome, because a confident wrong answer about insurance could cause real harm.

Grounding also strengthens security. Retrieved documents are treated as untrusted input and are sanitized against prompt-injection attempts, so text hidden inside an article cannot hijack the assistant's instructions. System instructions, your messages, retrieved passages, and tool results are kept separate rather than blended together, which prevents content in one channel from impersonating another. Together these measures mean the assistant can draw on outside knowledge while still resisting manipulation and staying faithful to reviewed sources.
