# Cost-Effectiveness Report for Project Garuda

Date checked: April 18, 2026

This report summarizes current commercial security-system pricing and subscription data gathered from official vendor pages, and frames the comparison in favor of Project Garuda for use in an IEEE Access manuscript.

## Project Garuda Baseline

- Total bill of materials: `₹38,085`
- Approximate USD equivalent: `$411`
- Ownership model: one-time hardware cost, no mandatory recurring subscription
- USD/INR reference used in this report: `1 USD = ₹92.60`
- Reference link provided by user: https://www.google.com/finance/beta/quote/USD-INR?sa=X&ved=2ahUKEwimuO-clveTAxUH1jgGHW9FBY4QmY0JegQIDRA0

## Executive Summary

Project Garuda is cost-effective because it delivers a broader security stack than mainstream consumer products at a lower or comparable total cost of ownership, while avoiding mandatory recurring subscription charges. Commercial alternatives generally follow one of two models:

1. They are relatively inexpensive at entry but require ongoing cloud or monitoring fees to unlock meaningful AI and history features.
2. They offer stronger local capability but at a significantly higher upfront hardware cost.

By contrast, Garuda provides:

- edge AI inference
- self-hosted system control
- authenticated remote access
- encrypted evidence handling
- zero mandatory annual service cost

at a one-time cost of `₹38,085`.

## Official Vendor Pricing and Feature Notes

### Ring

Relevant official sources:
- Outdoor Cam: https://ring.com/products/stick-up-cam-battery
- Ring Protect plans: https://ring.com/protect-plans
- Ring Alarm systems: https://ring.com/home-security-system

Collected information:
- Ring Outdoor Cam price: `$79.99`
- Ring Alarm Security Kit, 8-Piece: `$199.99`
- Ring Alarm Pro Security Kit, 8-Piece: observed at `$229.99` on current comparison page
- Ring Solo plan: `$4.99/mo` or `$49.99/yr`
- Ring Multi plan: `$9.99/mo` or `$99.99/yr`
- Ring AI Pro plan: `$19.99/mo` or `$199.99/yr`

Feature overlap with Garuda:
- remote access
- AI-assisted alerts
- optional professional monitoring
- event history

Important gaps vs Garuda:
- no custom detector pipeline
- no explicit anti-spoof stage
- subscription-heavy for meaningful AI features
- more cloud-centric than Garuda

Comparison configuration used in report:
- Ring Alarm Pro 8-piece + Outdoor Cam + AI-enabled plan
- Approx upfront: `$309.98`
- Approx annual: `$199.99/yr`

### Google Nest / Google Home Premium

Relevant official sources:
- Nest Cam battery: https://store.google.com/us/product/nest_cam_battery
- Google Home Premium / Nest Aware replacement: https://store.google.com/us/product/nest_aware
- Google Help page: https://support.google.com/googlenest/answer/9546397?hl=en

Collected information:
- Nest Cam battery price: `$179.99` listed on official store page
- Google Home Premium Standard: `$10/mo` or `$100/yr`
- Google Home Premium Advanced: `$20/mo` or `$200/yr`

Included features noted on official pages:
- event-based video history
- intelligent alerts
- familiar face alerts
- package detection
- search/video summaries on higher tier
- voice/Gemini integration

Important gaps vs Garuda:
- no custom object detector
- no anti-spoof stage
- useful long-term value depends on subscription
- cloud subscription is central to advanced functionality

### SimpliSafe

Relevant official sources:
- Outdoor Security Camera Bundle: https://simplisafe.com/outdoor-security-camera-bundle
- Monitoring plans: https://support.simplisafe.com/page/monitoring-plans
- Outdoor camera features: https://simplisafe.com/outdoor-security-camera
- Monitoring/features page: https://simplisafe.com/features-alarm-monitoring

Collected information:
- Outdoor Security Camera Bundle: `$149.99`
- Self Monitoring with camera recordings: `33¢/day`
- Core plan: `$1.10/day`
- Pro / Pro Plus range: `$1.66/day` to `$2.66/day`

Feature overlap with Garuda:
- remote access
- AI-assisted outdoor threat handling
- optional live agent intervention
- event recordings

Important gaps vs Garuda:
- no custom on-device detection stack
- no anti-spoof stage
- recurring fee model dominates meaningful monitoring value

### eufy

Relevant official sources:
- eufyCam S3 Pro 2-Cam Kit: https://www.eufy.com/products/t88921w1
- eufy cloud pricing (1 device): https://www.eufy.com/products/cb001m011

Collected information:
- eufyCam S3 Pro 2-Cam Kit: `$549.99`
- eufy cloud basic monthly 1 device: `$3.99`
- eufy cloud basic annual 1 device: `$39.99`
- eufy cloud basic monthly 2 devices: `$7.99`
- eufy cloud plus annual all-device plan: `$139.99`

Important eufy positioning:
- local storage
- local AI
- no mandatory monthly fee

Why it still helps Garuda:
- eufy is a strong local-first comparator
- Garuda is still cheaper upfront than the premium local eufy kit
- Garuda can be framed as delivering broader platform functionality for less money

### Reolink

Relevant official sources:
- Argus 4 Pro product page: https://reolink.com/us/product/argus-4-pro/
- Reolink cloud plan explainer: https://reolink.com/blog/reolink-subscription-cost/
- Reolink cloud support overview: https://support.reolink.com/hc/en-us/articles/900000642983-How-to-Subscribe-to-Reolink-Cloud-Plans-via-Reolink-Website/

Collected information:
- Argus 4 Pro official page strongly emphasizes:
  - local storage
  - no monthly fee for local storage
  - person/vehicle/animal detection
  - remote app alerts
  - optional cloud in selected countries
- Reolink blog pricing summary:
  - Standard cloud plan: `$6.99/mo`
  - single-device LTE plan example: `$5.99/mo`

Why Reolink matters:
- it is another local-first comparator showing that subscription-free local storage exists in the market
- however, the product family still does not clearly match Garuda’s custom local edge stack plus anti-spoof plus self-hosted system architecture

## Paper-Ready Cost Comparison Table

Approximate INR conversions below use `1 USD = ₹92.60`.

| System | Upfront Cost | Annual Subscription Needed for Full Value | 3-Year Cost | Garuda Advantage |
|---|---:|---:|---:|---|
| **Project Garuda** | **₹38,085** | **₹0 mandatory** | **₹38,085** | Full edge inference, self-hosted stack, no compulsory cloud fee |
| Ring Alarm Pro + Outdoor Cam + AI plan | ~₹28,704 (`$309.98`) | ~₹18,519/yr (`$199.99/yr`) | ~₹84,261 | Garuda is much cheaper over time and avoids subscription lock-in |
| Google Nest Cam + Google Home Premium Standard | ~₹16,667 (`$179.99`) | ~₹9,260/yr (`$100/yr`) | ~₹44,447 | Garuda is cheaper over 3 years while offering a broader local stack |
| Google Nest Cam + Google Home Premium Advanced | ~₹16,667 (`$179.99`) | ~₹18,520/yr (`$200/yr`) | ~₹72,251 | Garuda is far cheaper over 3 years and not cloud-dependent |
| SimpliSafe Outdoor Camera Bundle + Self Monitoring | ~₹13,889 (`$149.99`) | ~₹11,154/yr (`$120.45/yr`) | ~₹47,350 | Garuda is cheaper over 3 years and avoids recurring service fees |
| eufyCam S3 Pro 2-Cam Kit | ~₹50,969 (`$549.99`) | ₹0 mandatory | ~₹50,969 | Garuda is cheaper even before comparing features |

## Interpretation in Favor of Garuda

Garuda compares strongly against all three common commercial pricing models:

- Subscription-heavy cloud model:
  - Ring and Google keep entry cost manageable but shift meaningful long-term cost into annual plans.
  - Garuda avoids this entirely.

- Monitoring-centric security model:
  - SimpliSafe adds professional services, but its total cost rises quickly across multi-year ownership.
  - Garuda remains a one-time system cost.

- Premium local-AI model:
  - eufy avoids mandatory cloud fees, but its comparable hardware bundle is still substantially more expensive upfront than Garuda.

This makes Garuda especially attractive under a total-cost-of-ownership analysis. Even when a commercial product appears cheaper on day one, that advantage erodes or disappears once annual service charges are included. Over a 3-year period, Garuda is cheaper than most AI-enabled subscription ecosystems and remains competitive even against single-camera products that provide a much narrower feature set.

## Strong IEEE-Style Pro-Garuda Statements

Use any of the following as manuscript-ready prose.

### Version 1

Project Garuda achieves a favorable cost-performance position relative to commercial AI-enabled home-security products. Its total bill of materials is `₹38,085` with no mandatory recurring subscription, whereas commercial alternatives typically either (i) depend on annual cloud or monitoring plans to unlock meaningful AI functionality and event history, or (ii) require a higher upfront hardware investment for local-first operation. On a 3-year ownership horizon, Garuda undercuts subscription-centric systems such as Ring, Google Nest, and SimpliSafe, while also remaining less expensive than premium local-storage products such as eufyCam S3 Pro.

### Version 2

From a total-cost-of-ownership perspective, Garuda is more economical than mainstream AI-enabled consumer security ecosystems because it consolidates edge inference, remote access, event logging, and secure evidence handling into a single self-hosted platform at `₹38,085`, with no compulsory annual service charge.

### Version 3

Garuda is not merely a camera but a consolidated security platform. This distinction is economically important: commercial systems often distribute comparable capabilities across hardware purchase, cloud video plans, AI feature subscriptions, and professional monitoring tiers. Garuda collapses these into a single edge-resident deployment with no mandatory recurring fee, producing a lower long-term ownership cost.

## Recommended Feature Framing For the Paper

To justify the value proposition, compare Garuda against commercial systems on the following platform-level features:

- AI detection and classification
- remote access
- event history
- voice interaction
- authenticated user management
- encrypted evidence handling
- offline or local operation
- no mandatory cloud inference
- no recurring monitoring or storage fee

This framing supports the argument that Garuda should be evaluated against full commercial security ecosystems rather than bare camera hardware alone.

## Recommended Narrative Structure

Suggested sequence for the paper:

1. State Garuda’s upfront BOM: `₹38,085`.
2. State that many commercial systems use a low-entry/high-recurring-cost pricing model.
3. Present a 3-year total-cost-of-ownership comparison.
4. Emphasize zero mandatory subscription as a core economic advantage.
5. Conclude that Garuda is cost-effective not because it is the cheapest single device, but because it delivers platform-level functionality without subscription lock-in.

## Safe Claims vs Claims to Avoid

### Safe claims

- Garuda is cost-effective relative to mainstream AI-enabled subscription ecosystems.
- Garuda has no mandatory recurring subscription.
- Garuda is cheaper over a 3-year horizon than several commercial subscription-based alternatives.
- Garuda is cheaper upfront than at least one premium local-first comparator, eufyCam S3 Pro 2-Cam Kit.

### Claims to avoid

- Do not claim Garuda is the cheapest security product overall.
- Do not claim no commercial product shares any of its features.
- Do not claim universal absence of comparable systems without broader market proof.

## Source List

- Ring Outdoor Cam: https://ring.com/products/stick-up-cam-battery
- Ring Protect plans: https://ring.com/protect-plans
- Ring Alarm systems: https://ring.com/home-security-system
- Nest Cam: https://store.google.com/us/product/nest_cam_battery
- Google Home Premium: https://store.google.com/us/product/nest_aware
- Google Help on Google Home Premium: https://support.google.com/googlenest/answer/9546397?hl=en
- SimpliSafe Outdoor bundle: https://simplisafe.com/outdoor-security-camera-bundle
- SimpliSafe monitoring plans: https://support.simplisafe.com/page/monitoring-plans
- SimpliSafe outdoor camera features: https://simplisafe.com/outdoor-security-camera
- eufyCam S3 Pro 2-cam kit: https://www.eufy.com/products/t88921w1
- eufy cloud pricing: https://www.eufy.com/products/cb001m011
- Reolink Argus 4 Pro: https://reolink.com/us/product/argus-4-pro/
- Reolink subscription explainer: https://reolink.com/blog/reolink-subscription-cost/
