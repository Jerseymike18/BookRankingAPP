import { fetchBooks, fetchValidGenres } from "@/lib/api";
import PredictClient from "./PredictClient";

export default async function PredictPage() {
  const [data, genres] = await Promise.all([fetchBooks(), fetchValidGenres()]);
  return (
    <PredictClient
      books={data.books}
      validGenres={genres}
      categoryOrder={data.category_order}
    />
  );
}
