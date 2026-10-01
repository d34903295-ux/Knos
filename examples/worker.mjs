// A Knos worker: take paid jobs, do them with your own agent, get paid when the buyer accepts.
import { Knos } from "knos";

const myAgent = async (brief) => "...";  // call any agent or model here, on your own key

const k = await Knos.connect({ agent: "my-worker" });
await k.work(myAgent);
