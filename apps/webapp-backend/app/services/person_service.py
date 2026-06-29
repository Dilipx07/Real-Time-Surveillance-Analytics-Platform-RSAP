import asyncio
import pickle


async def extract_single_face_encoding(image: bytes) -> bytes:
    def work() -> bytes:
        import face_recognition

        decoded = face_recognition.load_image_file(__import__("io").BytesIO(image))
        locations = face_recognition.face_locations(decoded)
        if len(locations) != 1:
            raise ValueError("Face image must contain exactly one detectable face")
        encodings = face_recognition.face_encodings(decoded, known_face_locations=locations)
        if len(encodings) != 1:
            raise ValueError("A face encoding could not be generated")
        return pickle.dumps(encodings[0], protocol=pickle.HIGHEST_PROTOCOL)

    return await asyncio.to_thread(work)
