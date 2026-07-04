import { redirect } from "next/navigation";
import ComingSoon from "@/components/ComingSoon";
import { READONLY } from "@/lib/readonly";

export default function EditRatingsPage() {
  // Editing happens inline on the Rankings page. On a read-only deploy there's
  // no editing at all, so show the notice instead of redirecting into it.
  if (READONLY) {
    return <ComingSoon title="Edit Ratings" subtitle="Not available on the read-only public site." />;
  }
  redirect("/fiction/rankings");
}
