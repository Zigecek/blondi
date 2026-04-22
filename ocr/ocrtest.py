import cv2
import numpy as np
import re
from ultralytics import YOLO
import torch
import tempfile
import os
import difflib
import warnings
import urllib3

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=urllib3.exceptions.DependencyWarning)
original_load = torch.load


def patched_load(*args, **kwargs):
    kwargs['weights_only'] = False
    return original_load(*args, **kwargs)


torch.load = patched_load
from nomeroff_net import pipeline


def clean_raw_text(text: str) -> str:
    text = text.upper().strip()
    text = re.sub(r'[^A-Z0-9]', '', text)

    if len(text) < 5 or len(text) > 8:
        return ""

    return text


def preprocess_for_nomeroff(crop: np.ndarray) -> np.ndarray:
    height, width = crop.shape[:2]
    if width > 800 or height > 800:
        return crop

    scale_factor = 2.0
    large_crop = cv2.resize(crop, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC)

    return large_crop


class CzechPlateRecognizer:
    def __init__(self, yolo_model_path: str = 'license-plate-finetune-v1m.pt'):
        self.detector = YOLO(yolo_model_path)

        print("Nomeroff OCR Loading...")
        self.ocr_reader = pipeline(
            "number_plate_text_reading",
            image_loader="opencv",
            default_label="eu",
            default_lines_count=1
        )
        print("Nomeroff Net připraven.")

    def process_image(self, image_path: str):
        img = cv2.imread(image_path)

        if img is not None and len(img.shape) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

        if img is None:
            raise FileNotFoundError(f"Obrázek nenalezen: {image_path}")

        results = self.detector(img, verbose=False)[0]
        final_results = []

        print(f"\n--- Zpracovávám: {image_path} ---")

        regions_to_process = []
        valid_boxes = [box for box in results.boxes if float(box.conf[0]) >= 0]

        if valid_boxes:
            for box in valid_boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                regions_to_process.append({
                    "crop": img[y1:y2, x1:x2],
                    "bbox": [x1, y1, x2, y2],
                    "is_fallback": False
                })
        else:
            h, w = img.shape[:2]
            regions_to_process.append({
                "crop": img,
                "bbox": [0, 0, w, h],
                "is_fallback": True
            })

        for i, region in enumerate(regions_to_process):
            crop = region["crop"]
            bbox = region["bbox"]

            if crop.size == 0: continue

            processed_crop = preprocess_for_nomeroff(crop)

            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name

            try:
                cv2.imwrite(tmp_path, processed_crop)
                prediction = self.ocr_reader([(tmp_path, 'eu', 1, processed_crop)])

                raw_text = ""
                if prediction:
                    item = prediction[0]
                    if isinstance(item, tuple) or isinstance(item, list):
                        raw_text = str(item[0])
                    elif hasattr(item, 'text'):
                        raw_text = str(item.text)
                    else:
                        raw_text = str(item)

                cleaned = clean_raw_text(raw_text)

                label_prefix = "FALLBACK" if region["is_fallback"] else f"Box #{i}"
                print(f"\n{label_prefix}:")
                print(f"  Přečteno Nomeroffem: '{raw_text}'")
                print(f"  Očištěno: '{cleaned}'")

                if cleaned:
                    final_results.append({
                        "plate": cleaned,
                        "bbox": bbox,
                        "is_fallback": region["is_fallback"]
                    })

            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        print("-" * 30)
        return final_results


if __name__ == "__main__":
    recognizer = CzechPlateRecognizer(yolo_model_path='license-plate-finetune-v1m.pt')
    test_dir = "./test"

    if not os.path.exists(test_dir):
        print(f"Složka '{test_dir}' neexistuje.")
    else:
        total_images = 0
        exact_matches = 0
        fuzzy_matches = 0
        failed = 0

        FUZZY_THRESHOLD = 0.70

        for filename in os.listdir(test_dir):
            if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                continue

            filepath = os.path.join(test_dir, filename)
            ground_truth_raw = os.path.splitext(filename)[0]
            ground_truth = re.sub(r'[^A-Z0-9]', '', ground_truth_raw.upper())

            total_images += 1

            try:
                results = recognizer.process_image(filepath)
            except Exception as e:
                print(f"[CRASH] {filename}: {e}")
                failed += 1
                continue

            predicted_plates = [re.sub(r'[^A-Z0-9]', '', res.get("plate", "").upper()) for res in results if
                                res.get("plate")]

            if not predicted_plates:
                print(f"[ CHYB ] {filename:<15} -> Nic nenalezeno (Očekáváno: {ground_truth})")
                failed += 1
                continue

            if ground_truth in predicted_plates:
                exact_matches += 1
                print(f"[  OK  ] {filename:<15} -> Přečteno přesně: {ground_truth}")
                continue

            best_match = ""
            best_ratio = 0.0

            for pred in predicted_plates:
                # SequenceMatcher vrací poměr shody od 0.0 do 1.0
                ratio = difflib.SequenceMatcher(None, ground_truth, pred).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = pred

            if best_ratio >= FUZZY_THRESHOLD:
                fuzzy_matches += 1
                print(
                    f"[ ~OK~ ] {filename:<15} -> Částečná shoda, nejspíš OK: Přečteno '{best_match}' (Oček. '{ground_truth}', Shoda: {best_ratio * 100:.0f}%)")
            else:
                failed += 1
                print(
                    f"[ŠPATNĚ] {filename:<15} -> Žádná nebo přílíš malá shoda: Přečteno '{best_match}' (Oček. '{ground_truth}', Shoda: {best_ratio * 100:.0f}%)")

        # Závěrečné shrnutí
        if total_images > 0:
            exact_accuracy = (exact_matches / total_images) * 100
            usable_accuracy = ((exact_matches + fuzzy_matches) / total_images) * 100

            print(f"\n{'=' * 60}")
            print(f" VÝSLEDKY TESTOVÁNÍ:")
            print(f" Celkem obrázků:     {total_images}")
            print(f" ------------------------------------")
            print(f" Přesné shody (100%): {exact_matches} ({exact_accuracy:.1f} %)")
            print(f" Akceptovatelné (>{FUZZY_THRESHOLD * 100:.0f}%): {fuzzy_matches}")
            print(f" Špatně: {failed}")
            print(f" ------------------------------------")
            print(f" POUŽITELNÁ ÚSPĚŠNOST: {usable_accuracy:.1f} %")
            print(f"{'=' * 60}")