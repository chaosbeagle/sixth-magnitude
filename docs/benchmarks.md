# Visual capability behind Sixth Magnitude

[English introduction](../README.md) · [繁體中文介紹](../README.zh-TW.md)

Sixth Magnitude starts from the observation that a frontier model can retain useful visual capabilities at low thinking effort. The results below explain why Astra low is a useful starting point for the framework. They cover multimodal reasoning, finding targets on screen, and carrying out computer tasks.

Scores were checked against the primary sources on October 30, 2026. Each comparison uses one evaluator's results; scores from different benchmarks are not combined.

## A direct comparison at low effort

Artificial Analysis reports the following MMMU-Pro scores. MMMU-Pro tests multimodal academic reasoning; answering its questions requires more than simply recognizing objects.

| Model | Thinking effort | MMMU-Pro |
| --- | --- | ---: |
| [GPT-5.6 Sol](https://artificialanalysis.ai/models/gpt-5-6-sol-high) | high | 81.8% |
| [GPT-5.6 Sol](https://artificialanalysis.ai/models/gpt-5-6-sol) | max | 83.4% |
| [GPT-6 Astra](https://artificialanalysis.ai/models/gpt-6-astra-low) | low | **84.6%** |
| [GPT-6 Astra](https://artificialanalysis.ai/models/gpt-6-astra) | max | 86.9% |

Astra low is 2.8 percentage points above Sol high and 1.2 points above Sol max. It is 2.3 points below Astra max. Differences are calculated from the underlying scores before rounding.

Artificial Analysis's [evaluation methodology](https://artificialanalysis.ai/methodology/intelligence-benchmarking) specifies 1,730 questions with ten answer options, one repeat, pass@1 scoring, regular-expression answer extraction, and no tools. The table uses the `mmmuPro` values and explicit effort labels in the public model pages' chart data, rounded to one decimal place. It does not use the aggregate Artificial Analysis Intelligence Index.

This is direct evidence for Astra low's performance on a visual reasoning benchmark. It supports starting an inspection workflow at low effort, while leaving room to spend more reasoning effort on difficult cases. It does not establish that every visual task benefits equally or that low effort minimizes cost per correct result.

## Screen targeting and computer use

OpenAI's [Astra launch report](https://openai.com/index/gpt-6-astra/) provides two further comparisons:

| Evaluation | GPT-5.6 Sol | GPT-6 Astra |
| --- | ---: | ---: |
| ScreenSpot-Pro, no tools | 76.9% | 92.7% |
| OSWorld 2.0, v2026.08.08, offline set, partial score | 65.7% | 72.6% |

The report selects the highest score reached at any effort setting for each evaluation. These rows describe model-level results and should not be attributed to Astra low. OSWorld's partial score credits progress within tasks; 72.6% is not the percentage of tasks fully completed. See the [OSWorld 2.0 project](https://osworld-v2.xlang.ai/) for the distinction between partial and binary completion scores.

These evaluations cover abilities that an inspection agent needs beyond interpreting an image: locating a control and carrying work through a sequence of actions. They motivate the use of an existing agent environment with computer-use and coding tools.

## What this means for the workbench

The design inference is practical: keep visual judgment with the model, and give it tools for coordinate handling, image extraction, measurement, and persistent records. A short skill supplies the inspection procedure. The workbench lets the agent move between scales, save a region, and check the saved overlay against the image.

The public WSI demonstration exercises that workflow on two slides. Its observations remain candidates for expert review. No pathology accuracy, throughput, or cost advantage is estimated from those demonstrations or from the benchmarks above.

## Further reading

- [MMMU-Pro: A More Robust Multi-discipline Multimodal Understanding Benchmark](https://arxiv.org/abs/2409.02813) explains the benchmark's design, including reducing text-only shortcuts and increasing the number of answer choices.
- [GUI Grounding for Professional High-Resolution Computer Use](https://arxiv.org/abs/2504.07981) introduces ScreenSpot-Pro and its focus on locating targets in professional interfaces.
- [Managing usage with GPT-6 Astra in Work and Codex](https://help.openai.com/en/articles/20001516-managing-usage-with-gpt-6-astra-in-work-and-codex) discusses choosing effort levels. Its broad guidance that Astra low can outperform Sol high is consistent with the MMMU-Pro comparison; it is not a separate visual benchmark result.
