"""Extract the public benchmark media and write queries.json (text -> relevant file name) per benchmark."""
import ast, csv, json, os, sys, zipfile

D = "D:/roam_eval_public"
DL = D + "/dl"


def flat_extract(zpath, dest, keep=None):
    os.makedirs(dest, exist_ok=True)
    n = 0
    with zipfile.ZipFile(zpath) as z:
        for m in z.infolist():
            if m.is_dir():
                continue
            b = os.path.basename(m.filename)
            if not b or b.startswith("._") or "__MACOSX" in m.filename:
                continue
            if keep is not None and b not in keep:
                continue
            with z.open(m) as src, open(os.path.join(dest, b), "wb") as out:
                out.write(src.read())
            n += 1
    return n


def captions_csv(path):
    q = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for t in ast.literal_eval(r["raw"]):
                q.append(dict(text=t.strip(), file=r["filename"]))
    return q


which = sys.argv[1:]
if "flickr" in which:
    q = captions_csv(DL + "/flickr_1k_test_image_text_retrieval/test_1k_flickr.csv")
    n = flat_extract(DL + "/flickr_1k_test_image_text_retrieval/images_flickr_1k_test.zip", D + "/media/flickr")
    json.dump(q, open(D + "/flickr_queries.json", "w"))
    print("flickr", n, "files", len(q), "queries")
if "coco" in which:
    q = captions_csv(DL + "/mscoco_2014_5k_test_image_text_retrieval/test_5k_mscoco_2014.csv")
    n = flat_extract(DL + "/mscoco_2014_5k_test_image_text_retrieval/images_mscoco_2014_5k_test.zip", D + "/media/coco")
    json.dump(q, open(D + "/coco_queries.json", "w"))
    print("coco", n, "files", len(q), "queries")
if "msrvtt" in which:
    js = json.load(open(DL + "/MSR-VTT/msrvtt_test_1k.json", encoding="utf-8"))
    q = [dict(text=r["caption"].strip(), file=r["video"]) for r in js]
    n = flat_extract(DL + "/MSR-VTT/MSRVTT_Videos.zip", D + "/media/msrvtt", keep={r["video"] for r in js})
    json.dump(q, open(D + "/msrvtt_queries.json", "w"))
    print("msrvtt", n, "files", len(q), "queries")
