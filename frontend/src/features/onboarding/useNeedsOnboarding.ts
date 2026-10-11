import { useUser } from '../../api/queries'

/**
 * True when the panel should show onboarding instead of its empty state:
 * the profile has loaded and the reader hasn't finished or skipped it.
 * Gap callouts stay hidden while this is true (§4.6).
 */
export function useNeedsOnboarding(): boolean {
  const user = useUser()
  return !!user.data && !user.data.onboarded
}
