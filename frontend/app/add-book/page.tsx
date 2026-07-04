import { fetchBooks, fetchValidGenres } from "@/lib/api";
import AddBookClient from "./AddBookClient";
import ComingSoon from "@/components/ComingSoon";
import { READONLY } from "@/lib/readonly";

export default async function AddBookPage() {
  // Adding a book writes to the database — not available on a read-only deploy.
  if (READONLY) {
    return <ComingSoon title="Add a Book" subtitle="Not available on the read-only public site." />;
  }
  const [data, genres] = await Promise.all([fetchBooks(), fetchValidGenres()]);
  return <AddBookClient categoryOrder={data.category_order} validGenres={genres} />;
}
