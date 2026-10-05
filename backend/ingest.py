import chromadb
from chromadb.utils import embedding_functions

chroma_client = chromadb.PersistentClient(path="./chroma_store")
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = chroma_client.get_or_create_collection(
    name="anatomy_notes",
    embedding_function=embedding_fn,
)

sample_notes = [
    {
        "id": "note_1",
        "text": "The biceps brachii is a two-headed muscle located on the upper arm. "
                "It is responsible for flexion of the elbow and supination of the forearm.",
        "topic": "upper limb muscles",
    },
    {
        "id": "note_2",
        "text": "The femur is the longest and strongest bone in the human body. "
                "It extends from the hip to the knee and supports body weight during standing and walking.",
        "topic": "lower limb bones",
    },
    {
        "id": "note_3",
        "text": "The heart has four chambers: the right atrium, right ventricle, left atrium, "
                "and left ventricle. Blood flows from the right side to the lungs, and from the left side to the body.",
        "topic": "cardiovascular system",
    },
]


def ingest_sample_notes():
    if collection.count() > 0:
        return
    for note in sample_notes:
        collection.add(
            documents=[note["text"]],
            metadatas=[{"topic": note["topic"]}],
            ids=[note["id"]],
        )


if __name__ == "__main__":
    ingest_sample_notes()
    print("Sample notes ingested.")