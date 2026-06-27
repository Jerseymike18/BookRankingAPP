import { fetchBooks, fetchValidGenres } from "@/lib/api";
import AddBookClient from "./AddBookClient";

export default async function AddBookPage() {
  const [data, genres] = await Promise.all([fetchBooks(), fetchValidGenres()]);
  return <AddBookClient categoryOrder={data.category_order} validGenres={genres} />;
}
