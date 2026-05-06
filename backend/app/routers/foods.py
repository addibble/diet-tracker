from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.llm import parse_nutrition_label_image
from app.llm_tools.shared import fuzzy_score
from app.models import (
    Food,
    MealItem,
    MealItemOverride,
    Recipe,
    RecipeComponent,
)

router = APIRouter(prefix="/api/foods", tags=["foods"])


class FoodCreate(BaseModel):
    name: str
    brand: str | None = None
    serving_size_grams: float = 100
    calories_per_serving: float
    fat_per_serving: float
    saturated_fat_per_serving: float = 0
    cholesterol_per_serving: float = 0
    sodium_per_serving: float = 0
    carbs_per_serving: float
    fiber_per_serving: float = 0
    protein_per_serving: float
    source: str = "custom"


class FoodUpdate(BaseModel):
    name: str | None = None
    brand: str | None = None
    serving_size_grams: float | None = None
    calories_per_serving: float | None = None
    fat_per_serving: float | None = None
    saturated_fat_per_serving: float | None = None
    cholesterol_per_serving: float | None = None
    sodium_per_serving: float | None = None
    carbs_per_serving: float | None = None
    fiber_per_serving: float | None = None
    protein_per_serving: float | None = None
    source: str | None = None


class FoodImportResult(BaseModel):
    name: str
    brand: str | None = None
    serving_size_grams: float
    calories_per_serving: float
    fat_per_serving: float
    saturated_fat_per_serving: float
    cholesterol_per_serving: float
    sodium_per_serving: float
    carbs_per_serving: float
    fiber_per_serving: float
    protein_per_serving: float


@router.get("")
def list_foods(
    search: str | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    stmt = select(Food)
    if search:
        stmt = stmt.where(
            Food.name.contains(search) | Food.brand.contains(search)  # type: ignore[union-attr]
        )
    stmt = stmt.order_by(Food.name)
    return session.exec(stmt).all()


@router.get("/audit")
def audit_foods(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
    duplicate_threshold: float = Query(default=0.85, ge=0.5, le=1.0),
):
    """Audit foods for duplicates, missing macros, and unused entries."""
    return _audit_foods_impl(session, duplicate_threshold)


@router.get("/{food_id}")
def get_food(
    food_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    food = session.get(Food, food_id)
    if not food:
        raise HTTPException(status_code=404, detail="Food not found")
    return food


@router.post("", status_code=201)
def create_food(
    data: FoodCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    food = Food(**data.model_dump())
    session.add(food)
    session.commit()
    session.refresh(food)
    return food


@router.put("/{food_id}")
def update_food(
    food_id: int,
    data: FoodUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    food = session.get(Food, food_id)
    if not food:
        raise HTTPException(status_code=404, detail="Food not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(food, key, value)
    session.add(food)
    session.commit()
    session.refresh(food)
    return food


@router.delete("/{food_id}", status_code=204)
def delete_food(
    food_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    food = session.get(Food, food_id)
    if not food:
        raise HTTPException(status_code=404, detail="Food not found")
    session.delete(food)
    session.commit()


@router.post("/import-label", response_model=FoodImportResult)
async def import_food_label(
    image: UploadFile = File(...),
    model: str | None = Form(default=None),
    _user: str = Depends(get_current_user),
):
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Please upload an image file")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    try:
        result = await parse_nutrition_label_image(
            image_bytes=image_bytes,
            mime_type=image.content_type,
            model=model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Label OCR failed: {exc}")

    return FoodImportResult(**result)


# ── Merge & Audit ────────────────────────────────────────────────────


class FoodMergeRequest(BaseModel):
    source_id: int
    target_id: int


class FoodMergeResult(BaseModel):
    target_id: int
    source_id: int
    merged_meal_items: int
    merged_recipe_components: int
    merged_overrides_original: int
    merged_overrides_replacement: int


def _merge_foods(
    session: Session, source_id: int, target_id: int
) -> FoodMergeResult:
    if source_id == target_id:
        raise HTTPException(
            status_code=400, detail="source_id and target_id must differ"
        )
    source = session.get(Food, source_id)
    target = session.get(Food, target_id)
    if not source:
        raise HTTPException(
            status_code=404, detail=f"Source food {source_id} not found"
        )
    if not target:
        raise HTTPException(
            status_code=404, detail=f"Target food {target_id} not found"
        )

    # Cascade reassign references from source -> target
    meal_items = session.exec(
        select(MealItem).where(MealItem.food_id == source_id)
    ).all()
    for mi in meal_items:
        mi.food_id = target_id
        session.add(mi)

    rc_rows = session.exec(
        select(RecipeComponent).where(RecipeComponent.food_id == source_id)
    ).all()
    for rc in rc_rows:
        rc.food_id = target_id
        session.add(rc)

    ovr_orig = session.exec(
        select(MealItemOverride).where(
            MealItemOverride.original_food_id == source_id
        )
    ).all()
    for o in ovr_orig:
        o.original_food_id = target_id
        session.add(o)

    ovr_repl = session.exec(
        select(MealItemOverride).where(
            MealItemOverride.replacement_food_id == source_id
        )
    ).all()
    for o in ovr_repl:
        o.replacement_food_id = target_id
        session.add(o)

    session.flush()
    session.delete(source)
    session.commit()

    return FoodMergeResult(
        target_id=target_id,
        source_id=source_id,
        merged_meal_items=len(meal_items),
        merged_recipe_components=len(rc_rows),
        merged_overrides_original=len(ovr_orig),
        merged_overrides_replacement=len(ovr_repl),
    )


@router.post("/merge", response_model=FoodMergeResult)
def merge_foods(
    body: FoodMergeRequest,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Merge *source_id* into *target_id*: reassign all meal_items,
    recipe_components, and meal_item_overrides that reference the source
    to point at the target, then delete the source food."""
    return _merge_foods(session, body.source_id, body.target_id)


def _food_summary(f: Food, mi_count: int, rc_count: int) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "brand": f.brand,
        "serving_size_grams": f.serving_size_grams,
        "calories_per_serving": f.calories_per_serving,
        "fat_per_serving": f.fat_per_serving,
        "carbs_per_serving": f.carbs_per_serving,
        "protein_per_serving": f.protein_per_serving,
        "source": f.source,
        "meal_item_count": mi_count,
        "recipe_component_count": rc_count,
        "usage_count": mi_count + rc_count,
    }


def _audit_foods_impl(session: Session, duplicate_threshold: float) -> dict:
    """Audit foods for duplicates, missing macros, and unused entries."""
    foods = list(session.exec(select(Food)).all())

    # Build usage counts
    mi_rows = session.exec(select(MealItem.food_id)).all()
    rc_rows = session.exec(select(RecipeComponent.food_id)).all()
    mi_counts: dict[int, int] = {}
    for fid in mi_rows:
        if fid is not None:
            mi_counts[fid] = mi_counts.get(fid, 0) + 1
    rc_counts: dict[int, int] = {}
    for fid in rc_rows:
        rc_counts[fid] = rc_counts.get(fid, 0) + 1

    summaries = {
        f.id: _food_summary(
            f, mi_counts.get(f.id, 0), rc_counts.get(f.id, 0)
        )
        for f in foods
    }

    # Fuzzy duplicates: union-find over similarity ≥ threshold on name+brand
    parent: dict[int, int] = {f.id: f.id for f in foods}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    def key(f: Food) -> str:
        n = (f.name or "").strip().lower()
        b = (f.brand or "").strip().lower()
        return f"{n}|{b}" if b else n

    keys = {f.id: key(f) for f in foods}
    for i, f1 in enumerate(foods):
        for f2 in foods[i + 1:]:
            if fuzzy_score(keys[f1.id], keys[f2.id]) >= duplicate_threshold:
                union(f1.id, f2.id)

    groups: dict[int, list[int]] = {}
    for fid in parent:
        root = find(fid)
        groups.setdefault(root, []).append(fid)

    duplicate_groups = []
    for ids in groups.values():
        if len(ids) > 1:
            duplicate_groups.append({
                "size": len(ids),
                "foods": sorted(
                    [summaries[i] for i in ids],
                    key=lambda d: -d["usage_count"],
                ),
            })
    duplicate_groups.sort(key=lambda g: -g["size"])

    missing_macros = []
    for f in foods:
        if (f.calories_per_serving or 0) == 0 or (
            (f.fat_per_serving or 0) == 0
            and (f.carbs_per_serving or 0) == 0
            and (f.protein_per_serving or 0) == 0
        ):
            missing_macros.append(summaries[f.id])

    unused = [s for s in summaries.values() if s["usage_count"] == 0]

    return {
        "total_foods": len(foods),
        "duplicate_groups": duplicate_groups,
        "missing_macros": missing_macros,
        "unused": unused,
    }


# Audit endpoint for recipes lives on this router for proximity, but
# is exposed via the recipes router below by import.
def _audit_recipes(session: Session) -> dict:
    recipes = list(session.exec(select(Recipe)).all())
    components = list(session.exec(select(RecipeComponent)).all())
    foods_by_id = {
        f.id: f for f in session.exec(select(Food)).all()
    }

    by_recipe: dict[int, list[RecipeComponent]] = {}
    for c in components:
        by_recipe.setdefault(c.recipe_id, []).append(c)

    empty = []
    macro_anomalies = []
    for r in recipes:
        comps = by_recipe.get(r.id, [])
        if not comps:
            empty.append({"id": r.id, "name": r.name})
            continue
        total_cals = 0.0
        total_grams = 0.0
        valid = True
        for c in comps:
            f = foods_by_id.get(c.food_id)
            if not f or not f.serving_size_grams:
                valid = False
                break
            total_cals += (
                (c.amount_grams / f.serving_size_grams)
                * (f.calories_per_serving or 0)
            )
            total_grams += c.amount_grams
        if not valid or total_cals <= 0 or total_grams <= 0:
            macro_anomalies.append({
                "id": r.id,
                "name": r.name,
                "component_count": len(comps),
                "total_calories": round(total_cals, 1),
                "total_grams": round(total_grams, 1),
            })

    return {
        "total_recipes": len(recipes),
        "empty": empty,
        "macro_anomalies": macro_anomalies,
    }
