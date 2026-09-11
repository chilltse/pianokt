from typing import Protocol

class Recommender(Protocol):
    version: str
    def recommend(self,history:list[dict],preferences:list[str],candidates:list[dict],top_k:int)->list[dict]: ...

class DemoRecommender:
    version='demo-rule-v1-NOT-TRAINED'
    def recommend(self,history,preferences,candidates,top_k=5):
        last=history[-1] if history else {}
        accuracy=(last.get('summary') or {}).get('accuracy')
        target=0.3 if accuracy is None else max(0.1,min(0.8,float(accuracy)*0.6))
        def score(song): return -abs(song['difficulty']-target)+(0.2 if set(song['genres']) & set(preferences) else 0)
        return [dict(song_id=s['song_id'],title=s['title'],rank=i+1,score=score(s),predicted_accuracy=None,predicted_learning_gain=None,reason='demo difficulty/preference heuristic') for i,s in enumerate(sorted(candidates,key=lambda s:(-score(s),s['song_id']))[:top_k])]
