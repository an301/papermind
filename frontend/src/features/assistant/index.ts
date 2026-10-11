/**
 * The assistant panel (DESIGN.md §4.4, §5.9–5.20, §5.25), for the Reader to
 * mount inside its <aside aria-label="Assistant">:
 *
 *   import { AssistantPanel } from '../assistant'
 *   <aside aria-label="Assistant"><AssistantPanel /></aside>
 *
 * The panel fills its container (give the aside a height) and owns everything
 * inside it: the Depth header, the thread, the composer, the offline bar and
 * first-run onboarding. It reads the open paper from readerStore (`paperId`,
 * `meta`) and creates or restores the session itself. Chat state lives outside
 * React, so the panel can unmount (collapsed to the rail, sheet closed) while
 * an answer keeps streaming.
 *
 * `mode="peek"` is the phone bottom sheet's smallest detent (§4.4): the latest
 * answer's first line and the composer only.
 *
 * Bracket → chip (§5.8): call `focusCitationChip(messageId, label)` to scroll
 * the thread to the answer and focus the chip that made the bracket.
 */
export { AssistantPanel, type AssistantPanelProps } from './AssistantPanel'
export { focusCitationChip } from './focusChip'
