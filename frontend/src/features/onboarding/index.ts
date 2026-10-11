/**
 * First-run calibration (DESIGN.md §4.6). The assistant panel mounts
 * `<Onboarding />` the first time a reader opens any paper, while
 * `user.onboarded` is false; Settings can reopen it inline.
 */
export { Onboarding, type OnboardingProps } from './Onboarding'
export { LEVEL_OPTIONS, levelLabel, type LevelOption } from './levels'
export { useNeedsOnboarding } from './useNeedsOnboarding'
