import { fetchBooks } from "@/lib/api";
import PredictClient from "./PredictClient";

export default async function PredictPage() {
  const data = await fetchBooks();
  return <PredictClient categoryOrder={data.category_order} />;
}
