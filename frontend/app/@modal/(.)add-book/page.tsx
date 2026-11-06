import { fetchBooks, fetchValidGenres } from "@/lib/api";
import { getServerAccessToken } from "@/lib/supabase/server";
import AddBookClient from "@/app/add-book/AddBookClient";
import BottomSheet from "@/components/BottomSheet";
import { READONLY } from "@/lib/readonly";

export const dynamic = "force-dynamic";

// Intercepts a soft navigation to /add-book (the floating "+") and renders the
// same Add-a-Book form in a sheet over the current page. A direct visit or a
// reload of /add-book still gets the full page (app/add-book/page.tsx).
export default async function AddBookSheet() {
  if (READONLY) return null;
  const token = await getServerAccessToken();
  const [data, genres] = await Promise.all([
    fetchBooks("fiction", token),
    fetchValidGenres("fiction", token),
  ]);
  return (
    <BottomSheet label="Add a book">
      <AddBookClient categoryOrder={data.category_order} validGenres={genres} />
    </BottomSheet>
  );
}
