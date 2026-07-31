"use client";

import { createContext, useContext } from "react";
import { READONLY } from "@/lib/readonly";

/** Force read-only rendering for a subtree, independent of the global build flag.
 *
 * The public-profile pages render ANOTHER user's data with the SAME interactive
 * view components (RankingsView, the read-queue clients). Those components gate
 * their edit / mutation UI on the build-time `READONLY` flag — but on the hosted
 * app READONLY is false, and any mutation they fire targets the VIEWER's own
 * account, never the profile owner's. Wrapping a profile subtree in
 * `<ReadOnlyProvider value>` forces those affordances off without threading a
 * prop through every nested card component.
 *
 * Default is `false`, so `useReadOnly()` collapses to the plain global READONLY
 * for every existing (unwrapped) page — those renders stay byte-identical. */
const ReadOnlyContext = createContext(false);

export function ReadOnlyProvider({
  value,
  children,
}: {
  value: boolean;
  children: React.ReactNode;
}) {
  return <ReadOnlyContext.Provider value={value}>{children}</ReadOnlyContext.Provider>;
}

/** True when the build is a read-only deploy OR the nearest provider forces it.
 * Call at the top of a component body like any hook. */
export function useReadOnly(): boolean {
  const override = useContext(ReadOnlyContext);
  return READONLY || override;
}
