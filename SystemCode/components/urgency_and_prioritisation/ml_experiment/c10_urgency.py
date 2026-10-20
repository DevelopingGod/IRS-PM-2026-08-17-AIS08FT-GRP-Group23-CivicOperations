"""Standalone C10 urgency prototype: September NYC 311, draft or reviewed targets."""
import argparse
import hashlib
import json
import time
import warnings
from argparse import Namespace
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import MultinomialNB
from sklearn.svm import LinearSVC
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, cohen_kappa_score, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

LEVELS = ['Low', 'Medium', 'High', 'Critical']
FEATURES = ['complaint_type', 'descriptor', 'descriptor_2', 'location_type']
SOURCE_COLUMNS = ['unique_key', 'created_date', *FEATURES, 'borough', 'incident_address']
SCHEMA_VERSION = 1
ALGORITHMS = ['dummy', 'logistic_regression', 'multinomial_nb', 'linear_svm', 'random_forest']


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')


def read_csv(path, source=False):
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding='utf-8-sig',
                     usecols=(lambda c: c.strip().lower() in SOURCE_COLUMNS) if source else None)
    df.columns = df.columns.str.strip().str.lower()
    if df.columns.duplicated().any():
        raise ValueError('Duplicate column headers after normalization.')
    if 'unique_key' not in df or df.unique_key.str.strip().eq('').any():
        raise ValueError('Every row needs a nonblank unique_key.')
    df['unique_key'] = df.unique_key.str.strip()
    if df.unique_key.duplicated().any():
        raise ValueError('Duplicate unique_key values; resolve duplicates first.')
    return df


def features(df):
    if 'complaint_type' not in df:
        raise ValueError('Missing complaint_type.')
    x = df.reindex(columns=FEATURES, fill_value='').fillna('').astype(str)
    for col in FEATURES:
        x[col] = x[col].str.strip().str.lower().replace('', 'unknown')
    x['text'] = ('complaint ' + x.complaint_type + ' descriptor ' + x.descriptor
                 + ' additional ' + x.descriptor_2)
    return x


def pipeline(algorithm='logistic_regression'):
    classifiers = {
        'dummy': DummyClassifier(strategy='most_frequent'),
        'logistic_regression': LogisticRegression(class_weight='balanced', max_iter=1000, random_state=42),
        'multinomial_nb': MultinomialNB(alpha=1.0),
        'linear_svm': LinearSVC(class_weight='balanced', max_iter=5000, random_state=42),
        'random_forest': RandomForestClassifier(n_estimators=200, class_weight='balanced',
                                               n_jobs=-1, random_state=42),
    }
    if algorithm not in classifiers:
        raise ValueError('Unknown algorithm: ' + algorithm)
    return Pipeline([
        ('encode', ColumnTransformer([
            ('text', TfidfVectorizer(ngram_range=(1, 2), max_features=30000,
                                    sublinear_tf=True), 'text'),
            ('location', OneHotEncoder(handle_unknown='ignore'), ['location_type']),
        ], sparse_threshold=1.0)),
        ('classifier', classifiers[algorithm]),
    ])


def model_scores(model, x, classes):
    """Return aligned scores and their semantics; never turn SVM margins into probabilities."""
    order = [list(model.classes_).index(c) for c in classes]
    if hasattr(model, 'predict_proba'):
        return model.predict_proba(x)[:, order], 'uncalibrated_probability'
    scores = np.asarray(model.decision_function(x))
    if scores.ndim == 1:
        scores = np.column_stack([-scores, scores])
    return scores[:, order], 'decision_margin'


def incident_groups(df):
    """Approximate address/day/category groups, not verified duplicate detection."""
    address = df.get('incident_address', pd.Series('', index=df.index)).str.strip().str.lower()
    day = df.created_date.str[:10]
    category = df.complaint_type.str.strip().str.lower()
    return pd.Series([hashlib.sha256(json.dumps([a, d, c]).encode()).hexdigest()
                      if a and d else 'id:' + key
                      for a, d, c, key in zip(address, day, category, df.unique_key)], index=df.index)


def prepare(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)

    df = read_csv(args.data, source=True)

    needed = ['created_date', *FEATURES]

    if any(c not in df for c in needed):
        raise ValueError(
            'Source missing required columns: ' +
            ', '.join(c for c in needed if c not in df)
        )

    dates = pd.to_datetime(df.created_date, errors='coerce')

    valid = dates.ge('2026-01-01') & dates.lt('2026-10-01')

    if not valid.all():
        raise ValueError(
            'Expected only January–September 2026 records with valid Created Dates.'
        )

    if df.complaint_type.str.strip().eq('').any():
        raise ValueError(
            'Blank complaint types require review before preparation.'
        )

    policy = json.loads(Path(args.policy).read_text(encoding='utf-8'))
    
    if df.complaint_type.str.strip().eq('').any():
        raise ValueError('Blank complaint types require review before preparation.')
    policy = json.loads(Path(args.policy).read_text(encoding='utf-8'))
    if policy['default_level'] not in LEVELS[:3]:
        raise ValueError('Draft default must be Low, Medium or High.')
    x = features(df)
    labels = pd.DataFrame({'unique_key': df.unique_key,
                           'draft_urgency': policy['default_level'],
                           'draft_reason': policy['default_reason'],
                           'draft_review_status': 'draft_requires_review'})
    matched = pd.Series(False, index=df.index)
    for rule in policy['rules']:
        if rule['level'] not in LEVELS[:3] or rule['field'] not in FEATURES:
            raise ValueError('Draft rules must use supported input fields and three draft levels.')
        mask = (~matched) & x[rule['field']].isin([str(v).strip().lower() for v in rule['values']])
        labels.loc[mask, ['draft_urgency', 'draft_reason']] = [rule['level'], rule['reason']]
        matched |= mask
    df.to_csv(out / 'september_requests.csv', index=False)
    labels.to_csv(out / 'draft_labels.csv', index=False)
    # Coverage-oriented review sample: reserve examples of every category,
    # then draw the remainder randomly. It is not a prevalence estimate.
    review_idx = df.groupby('complaint_type', group_keys=False).head(0).index
    selected = df.groupby('complaint_type', group_keys=False).sample(n=1, random_state=42).index
    available = df.index.difference(selected)
    extra = df.loc[available].sample(n=min(max(args.review_size-len(selected), 0), len(available)),
                                    random_state=42).index
    review_idx = selected.union(extra)
    review = df.loc[review_idx].merge(labels, on='unique_key', validate='one_to_one')
    review = review.drop(columns=['incident_address'], errors='ignore')
    review['reviewed_urgency'] = ''
    review['reviewer_id'] = ''
    review['reviewer_rationale'] = ''
    review.to_csv(out / 'urgency_review.csv', index=False)
    profile = {'source_file': Path(args.data).name, 'rows': len(df),
               'unique_requests': df.unique_key.nunique(), 'first_created': df.created_date.min(),
               'last_created': df.created_date.max(), 'source_sha256': file_hash(args.data),
               'draft_counts': labels.draft_urgency.value_counts().to_dict(),
               'complaint_counts': df.complaint_type.value_counts().to_dict(),
               'feature_blanks': df[FEATURES].eq('').sum().to_dict(),
               'review_sample_rows': len(review), 'review_sample_method': 'one per complaint category plus random remainder',
               'limitations': ['September snapshot ends September 29, not a complete month.',
                               'Draft labels are project assumptions, not official NYC or reviewed urgency.',
                               'Default Medium indicates unassessed context, not established moderate risk.',
                               'Original citizen narrative and external dependencies are unavailable.']}
    write_json(out / 'data_profile.json', profile)
    write_json(out / 'policy_used.json', policy)
    print(f'Prepared {len(df):,} September records and {len(review):,} review examples in {out}.')


def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def join_labels(data, label_path, mode):
    labels = read_csv(label_path)
    target = 'draft_urgency' if mode == 'draft' else 'reviewed_urgency'
    if target not in labels:
        raise ValueError(f'Labels need {target}; draft and reviewed labels are never interchanged.')
    labels[target] = labels[target].str.strip()
    labels = labels[labels[target].ne('')].copy()
    if labels.empty:
        raise ValueError(f'No {target} labels. Fill reviewed labels or explicitly opt into draft training.')
    if not labels[target].isin(LEVELS if mode == 'reviewed' else LEVELS[:3]).all():
        raise ValueError('Invalid urgency label; use exact Low, Medium, High or Critical (reviewed only).')
    if mode == 'draft':
        if 'draft_review_status' not in labels or labels.draft_review_status.ne('draft_requires_review').any():
            raise ValueError('Draft targets need draft_requires_review provenance.')
    elif 'reviewer_id' not in labels or labels.reviewer_id.str.strip().eq('').any():
        raise ValueError('Each reviewed label needs reviewer_id provenance.')
    if not set(labels.unique_key).issubset(set(data.unique_key)):
        raise ValueError('Labels contain IDs absent from source data.')
    return data.merge(labels[['unique_key', target]], on='unique_key', validate='one_to_one'), target


def metrics(y, pred, prob, classes):
    present = [c for c in classes if c in set(y)]
    report = classification_report(y, pred, labels=classes, output_dict=True, zero_division=0)
    for c in classes:
        if report[c]['support'] == 0:
            report[c]['recall'] = None
    macro = classification_report(y, pred, labels=present, output_dict=True, zero_division=0)['macro avg']['f1-score']
    kappa = cohen_kappa_score(y, pred, labels=LEVELS, weights='quadratic') if len(set(y))>1 else None
    truth = np.array([[int(v==c) for c in classes] for v in y])
    return {'class_order': classes, 'classification_report': report,
            'macro_f1_present_test_classes': macro,
            'absent_test_classes': [c for c in classes if c not in present],
            'confusion_matrix': confusion_matrix(y, pred, labels=classes).tolist(),
            'high_recall': report.get('High', {}).get('recall'),
            'critical_recall': report.get('Critical', {}).get('recall'),
            'critical_false_negative_rate': (1-report['Critical']['recall']) if 'Critical' in present else None,
            'weighted_kappa_quadratic': float(kappa) if kappa is not None and np.isfinite(kappa) else None,
            'macro_precision_present_test_classes': classification_report(y, pred, labels=present, output_dict=True, zero_division=0)['macro avg']['precision'],
            'macro_recall_present_test_classes': classification_report(y, pred, labels=present, output_dict=True, zero_division=0)['macro avg']['recall'],
            'macro_f1_all_four_levels': classification_report(y, pred, labels=LEVELS, output_dict=True, zero_division=0)['macro avg']['f1-score'] if set(classes)==set(LEVELS) and set(y)==set(LEVELS) else None,
            'accuracy': float(np.mean(np.asarray(y)==np.asarray(pred))),
            'brier_sum_0_to_2': float(np.mean(np.sum((prob-truth)**2, axis=1))) if prob is not None else None}


def training_data(args):
    if args.max_train_rows < 0:
        raise ValueError('--max-train-rows must be nonnegative; 0 uses all eligible rows.')
    if args.mode=='draft' and not args.acknowledge_draft_labels:
        raise ValueError('Draft training requires --acknowledge-draft-labels.')
    df, target = join_labels(read_csv(args.data, source=True), args.labels, args.mode)
    if 'created_date' not in df or 'complaint_type' not in df:
        raise ValueError('Training requires created_date and complaint_type.')
    dates = pd.to_datetime(df.created_date, errors='coerce')
    if dates.isna().any():
        raise ValueError('Invalid Created Dates prevent chronological splitting.')
    cutoff = pd.Timestamp(args.test_from)
    train_df, test_df = df[dates<cutoff].copy(), df[dates>=cutoff].copy()
    if train_df.empty or test_df.empty:
        raise ValueError('Chronological split is empty; adjust --test-from or review labels on both sides.')
    groups = set(incident_groups(train_df))
    test_df = test_df[~incident_groups(test_df).isin(groups)]
    classes = [c for c in LEVELS if c in set(df[target])]
    if len(classes)<2:
        raise ValueError('At least two supported urgency classes are needed.')
    for name, part in [('train',train_df),('test',test_df)]:
        counts = part[target].value_counts().reindex(classes, fill_value=0)
        if counts.min()<5:
            raise ValueError(f'Prototype split gate: {name} needs at least 5 labels per class; counts={counts.to_dict()}')
    eligible_train = len(train_df)
    if args.max_train_rows and len(train_df)>args.max_train_rows:
        # Class-proportional sample with a minimum for each class; reproducible.
        selected=[]
        for c, part in train_df.groupby(target):
            n=max(5, int(args.max_train_rows*len(part)/eligible_train))
            selected.append(part.sample(n=min(n,len(part)), random_state=42))
        train_df = pd.concat(selected).sort_values('created_date')
    return train_df, test_df, target, eligible_train


def train(args, prepared=None):
    algorithm = getattr(args, 'algorithm', 'logistic_regression')
    out=Path(args.output)
    if out.exists():
        raise ValueError('Training output exists; choose a new folder.')
    train_df, test_df, target, eligible_train = prepared if prepared is not None else training_data(args)
    started = time.perf_counter()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        model = pipeline(algorithm).fit(features(train_df), train_df[target])
    training_seconds = time.perf_counter()-started
    classes = [c for c in LEVELS if c in model.classes_]
    x_test = features(test_df)
    started = time.perf_counter()
    scores, score_type = model_scores(model, x_test, classes)
    pred = model.predict(x_test)
    prediction_seconds = time.perf_counter()-started
    prob = scores if score_type=='uncalibrated_probability' else None
    baseline = DummyClassifier(strategy='most_frequent').fit(features(train_df),train_df[target])
    bp = baseline.predict_proba(x_test)[:,[list(baseline.classes_).index(c) for c in classes]]
    status = 'draft_policy_imitation_not_validated' if args.mode=='draft' else 'reviewed_label_research_prototype'
    bundle = {'schema_version':SCHEMA_VERSION,'algorithm':algorithm,'score_type':score_type,'model':model,'classes':classes,'features':FEATURES,
              'sklearn_version':sklearn.__version__,'status':status,'label_mode':args.mode,
              'target_column':target,'train_ids':train_df.unique_key.tolist(),
              'train_groups':incident_groups(train_df).tolist(),
              'known_categories':sorted(features(train_df).complaint_type.unique()),
              'baseline':baseline,'test_from':args.test_from,
              'source_sha256':file_hash(args.data),'labels_sha256':file_hash(args.labels)}
    out.mkdir(parents=True,exist_ok=False)
    joblib.dump(bundle,out/'urgency_model.joblib')
    report={'algorithm':algorithm,'score_type':score_type,'training_seconds':training_seconds,
            'prediction_seconds':prediction_seconds,'training_warnings':[str(w.message) for w in caught],
            'classifier_parameters':model.named_steps['classifier'].get_params(),
            'preprocessing':{'text_fields':['complaint_type','descriptor','descriptor_2'],
                             'text_encoding':'TF-IDF','ngram_range':[1,2],'max_features':30000,
                             'sublinear_tf':True,'location_encoding':'one-hot, unknown ignored'},
            'random_seed':42,'sklearn_version':sklearn.__version__,
            'source_sha256':bundle['source_sha256'],'labels_sha256':bundle['labels_sha256'],
            'status':status,'target_column':target,'split':'chronological, with approximate incident overlap removed',
            'test_from':args.test_from,'eligible_training_rows':eligible_train,'training_rows':len(train_df),
            'test_rows':len(test_df),'train_counts':train_df[target].value_counts().to_dict(),
            'test_counts':test_df[target].value_counts().to_dict(),
            'unsupported_classes':[c for c in LEVELS if c not in classes],
            'model':metrics(test_df[target],pred,prob,classes),
            'majority_baseline':metrics(test_df[target],baseline.predict(x_test),bp,classes),
            'limitations':['Draft-mode metrics measure policy imitation, not actual urgency accuracy.' if args.mode=='draft' else 'Reviewed labels remain project judgments; independent validation is needed.',
                           'Class scores are uncalibrated, not validated confidence.',
                           'Repeated categories and descriptors can occur across dates.',
                           'The address/day/category grouping is only approximate.',
                           'Only submission category, descriptors and location type are model features.',
                           'Missing dependencies and Critical support are explicitly reported.']}
    manifest=pd.concat([train_df.assign(split='train'),test_df.assign(split='test')])
    manifest[['unique_key','created_date',target,'split']].to_csv(out/'split_manifest.csv',index=False)
    report['split_manifest_sha256']=file_hash(out/'split_manifest.csv')
    write_json(out/'metrics.json',report)
    pd.DataFrame(report['model']['confusion_matrix'],index=classes,columns=classes).rename_axis('actual_urgency').to_csv(out/'confusion_matrix.csv')
    pd.DataFrame([{'urgency_level':c,**report['model']['classification_report'][c]} for c in classes]).to_csv(out/'per_class_metrics.csv',index=False)
    holdout=test_df[['unique_key','created_date','complaint_type',target]].copy()
    holdout['predicted_urgency']=pred
    for i,c in enumerate(classes):holdout['score_'+c.lower()]=scores[:,i]
    holdout['score_type']=score_type
    holdout.to_csv(out/'holdout_predictions.csv',index=False)
    print(f'{algorithm}: trained {len(train_df):,} rows; tested {len(test_df):,}. Status: {status}.')
    return report


def compare(args):
    """Run fixed candidates on one shared split, without auto-selecting a production model."""
    out=Path(args.output)
    if out.exists():
        raise ValueError('Comparison output exists; choose a new folder.')
    prepared=training_data(args)
    out.mkdir(parents=True)
    rows=[]
    for algorithm in ALGORITHMS:
        run=Namespace(**vars(args));run.algorithm=algorithm;run.output=out/algorithm
        report=train(run, prepared=prepared)
        m=report['model']
        rows.append({'algorithm':algorithm,'status':report['status'],
                     'training_rows':report['training_rows'],'test_rows':report['test_rows'],
                     'split_manifest_sha256':report['split_manifest_sha256'],
                     **{k:m[k] for k in ['accuracy','macro_precision_present_test_classes',
                         'macro_recall_present_test_classes','macro_f1_present_test_classes',
                         'macro_f1_all_four_levels','high_recall','critical_recall',
                         'critical_false_negative_rate','weighted_kappa_quadratic','brier_sum_0_to_2']},
                     'training_seconds':report['training_seconds'],
                     'prediction_seconds':report['prediction_seconds'],
                     'score_type':report['score_type'],
                     'training_warnings':' | '.join(report['training_warnings'])})
        # Preserve completed runs if a later algorithm is interrupted.
        pd.DataFrame(rows).to_csv(out/'comparison.csv',index=False)
        write_json(out/'comparison.json',{'completed_algorithms':[r['algorithm'] for r in rows],
            'complete':len(rows)==len(ALGORITHMS),'results':rows,
            'interpretation':'Development holdout comparison. Select or tune with validation data; use a separate untouched test set for final reporting. Draft results measure imitation of draft labels.'})
    if len({r['split_manifest_sha256'] for r in rows})!=1:
        raise ValueError('Comparison split mismatch.')
    print(f'Saved all five results to {out / "comparison.csv"}. No model was automatically selected.')

def apply_critical_override(ml_prediction, external_factor_score=None):
    """
    Apply the Critical urgency override using the External Factors score.

    Rules:
        1    -> Critical
        0    -> Retain ML prediction
        None -> Retain ML prediction (external score unavailable)
    """

    valid_levels = ("Low", "Medium", "High", "Critical")

    if ml_prediction not in valid_levels:
        raise ValueError(
            "ML prediction must be Low, Medium, or High."
        )

    if external_factor_score not in (0, 1, None):
        raise ValueError(
            "External Factors score must be 0, 1, or None."
        )

    external_available = external_factor_score is not None

    if external_factor_score == 1:
        return {
            "ml_prediction": ml_prediction,
            "external_factor_score": external_factor_score,
            "external_factors_available": True,
            "final_urgency": "Critical",
            "critical_override_applied": True,
            "decision_method": "External Factors Rule",
            "reason": "External Factors score is 1."
        }

    return {
        "ml_prediction": ml_prediction,
        "external_factor_score": external_factor_score,
        "external_factors_available": external_available,
        "final_urgency": ml_prediction,
        "critical_override_applied": False,
        "decision_method": "Machine Learning",
        "reason": (
            "External Factors score is 0."
            if external_available
            else "External Factors score unavailable; ML prediction retained."
        )
    }

class UrgencyAssigner:
    """C10 adapter. Optional dependency context is recorded, never fabricated."""
    def __init__(self, model_path):
        # joblib/pickle is executable: load only your own trusted model files.
        self.bundle=joblib.load(model_path)
        if self.bundle.get('schema_version')!=SCHEMA_VERSION:
            raise ValueError('Unsupported model schema.')
        if self.bundle['sklearn_version']!=sklearn.__version__:
            raise ValueError('Use scikit-learn '+self.bundle['sklearn_version']+' for this model.')
        self.model=self.bundle['model'];self.classes=self.bundle['classes']

    def assign_urgency(self, request, emergency_result=None, external_factors=None):
        if emergency_result is not None and not isinstance(emergency_result,dict):
            raise ValueError('emergency_result must be an object or None.')
        if external_factors is not None and not isinstance(external_factors,dict):
            raise ValueError('external_factors must be an object or None.')
        emergency=(emergency_result or {}).get('is_emergency')
        if emergency is not None and type(emergency) is not bool:
            raise ValueError('is_emergency must be true, false or null.')
        missing=[]
        if emergency is None:missing.append('emergency_check')
        if not external_factors:missing.append('external_factors')
        for f in ['hazard_indicator','sensitive_site_flag','people_at_risk','external_event_severity']:
            if (external_factors or {}).get(f) is None:missing.append(f)
        for f in FEATURES:
            if not str(request.get(f,'') or '').strip():missing.append(f)
        result={'unique_key':request.get('unique_key'),'urgency_level':None,
                'algorithm':self.bundle.get('algorithm','logistic_regression'),
                'score_type':self.bundle.get('score_type','uncalibrated_probability'),
                'class_scores':{c:None for c in LEVELS},'manual_review_flag':True,
                'missing_context':missing,'unsupported_classes':[c for c in LEVELS if c not in self.classes],
                'critical_supported': 'Critical' in self.classes,
                'critical_rule_supported': True,
                'critical_support_method': 'External Factors binary rule','model_status':self.bundle['status'],
                'context_applied_to_model':False,'external_context':external_factors,
                'emergency_result':emergency_result,'reasons':[],'supporting_model_features':[]}
        if emergency is True:
            result.update(status='emergency_redirect',reasons=['Emergency Check flagged this request; non-emergency urgency prediction was bypassed.'])
            return result
        if 'complaint_type' in missing:
            result.update(status='insufficient_input',reasons=['Complaint type is missing; no urgency prediction was made.'])
            return result
        x=features(pd.DataFrame([request]))
        scores,score_type=model_scores(self.model,x,self.classes)
        predicted=str(self.model.predict(x)[0])
                # Preserve the original machine learning prediction
        result['ml_prediction'] = predicted

        # Read the binary score supplied by External Factors (C08)
        external_factor_score = (
            external_factors.get('external_factor_score')
            if external_factors is not None
            else None
        )

        # Apply the Critical urgency rule
        decision = apply_critical_override(
            ml_prediction=predicted,
            external_factor_score=external_factor_score
        )

        # Record the final urgency and decision details
        result['urgency_level'] = decision['final_urgency']
        result['external_factor_score'] = decision['external_factor_score']
        result['external_factors_available'] = decision['external_factors_available']
        result['critical_override_applied'] = decision['critical_override_applied']
        result['decision_method'] = decision['decision_method']
        result['critical_decision_reason'] = decision['reason']


        result['status'] = 'draft_prediction' if self.bundle['label_mode'] == 'draft' else 'reviewed_model_prediction'

        result['score_type']=score_type
        for c,p in zip(self.classes,scores[0]):result['class_scores'][str(c)]=float(p)
        result['reasons']=['Prediction uses complaint type, descriptors and location type only.',
                           'Class scores are decision margins, not probabilities.' if score_type=='decision_margin' else 'Class scores are uncalibrated model outputs.']
        if result['algorithm']=='dummy':
            result['reasons'][0]='Majority-class baseline; prediction does not depend on request features.'
        if self.bundle['label_mode']=='draft':result['reasons'].append('This model learned provisional draft rules, not verified real-world urgency.')
        if emergency is None:result['reasons'].append('Emergency screening is unavailable; this result does not establish that the request is non-emergency.')
        if external_factors:result['reasons'].append('External context is recorded for review but was not used by this model.')
        if x.complaint_type.iloc[0] not in self.bundle['known_categories']:
            result['reasons'].append('Complaint category was not seen during training; review is required.')
        clf=self.model.named_steps['classifier'];idx=list(clf.classes_).index(predicted)
        if not hasattr(clf,'coef_'):
            result['reasons'].append('Local feature contributions are not implemented for this algorithm.')
            return result
        # Linear score contributions, not causal evidence about a hazard.
        transformed=self.model.named_steps['encode'].transform(x)
        coef=clf.coef_[idx] if len(clf.classes_)>2 else clf.coef_[0]*(1 if idx==1 else -1)
        values=transformed.toarray() if hasattr(transformed,'toarray') else np.asarray(transformed)
        contrib=values[0]*coef
        names=self.model.named_steps['encode'].get_feature_names_out()
        top=np.argsort(contrib)[::-1][:5]
        contribution_key='margin_contribution' if score_type=='decision_margin' else 'logit_contribution'
        result['supporting_model_features']=[{'feature':str(names[i]),contribution_key:float(contrib[i])}
                                              for i in top if contrib[i]>0]
        return result

    def predict_batch(self, df):
        x=features(df);p,score_type=model_scores(self.model,x,self.classes)
        out=df.reindex(columns=['unique_key','created_date','complaint_type']).copy()
        out['predicted_urgency']=self.model.predict(x)
        out['score_type']=score_type
        out['algorithm']=self.bundle.get('algorithm','logistic_regression')
        for c in LEVELS:
            out['score_'+c.lower()]=p[:,self.classes.index(c)] if c in self.classes else np.nan
        blank=df.complaint_type.fillna('').str.strip().eq('')
        out.loc[blank,'predicted_urgency']=''
        out.loc[blank,['score_'+c.lower() for c in LEVELS]]=np.nan
        out['manual_review_flag']=True
        out['critical_supported']='Critical' in self.classes
        out['emergency_check_status']='unavailable'
        out['external_factors_status']='unavailable'
        out['model_status']=self.bundle['status']
        out['unseen_complaint_type']=~x.complaint_type.isin(self.bundle['known_categories'])
        out['assessment_status']=np.where(blank,'insufficient_input','provisional_prediction')
        return out


def predict(args):
    component=UrgencyAssigner(args.model);out=Path(args.output)
    if out.exists():raise ValueError('Prediction output exists; select a new filename.')
    count=0
    for i,chunk in enumerate(pd.read_csv(args.data,dtype=str,keep_default_na=False,
                                         encoding='utf-8-sig',chunksize=10000)):
        chunk.columns=chunk.columns.str.strip().str.lower()
        if chunk.columns.duplicated().any():raise ValueError('Duplicate headers.')
        result=component.predict_batch(chunk);result.to_csv(out,index=False,mode='w' if i==0 else 'a',header=i==0)
        count+=len(chunk)
    print(f'Predicted {count:,} requests to {out}. All need human review.')


def assess(args):
    payload=json.loads(Path(args.request).read_text(encoding='utf-8'))
    result=UrgencyAssigner(args.model).assign_urgency(payload['request'],payload.get('emergency_result'),payload.get('external_factors'))
    print(json.dumps(result,indent=2,allow_nan=False))


def evaluate(args):
    component=UrgencyAssigner(args.model)
    df,target=join_labels(read_csv(args.data,source=True),args.labels,'reviewed')
    if set(df.unique_key)&set(component.bundle['train_ids']):raise ValueError('Evaluation contains training IDs.')
    if set(incident_groups(df))&set(component.bundle['train_groups']):raise ValueError('Evaluation contains training incident groups.')
    if not set(df[target]).issubset(set(component.classes)):raise ValueError('Evaluation includes unsupported classes; add reviewed examples and retrain first.')
    x=features(df);scores,score_type=model_scores(component.model,x,component.classes)
    p=scores if score_type=='uncalibrated_probability' else None
    pred=component.model.predict(x)
    out=Path(args.output)
    if out.exists():raise ValueError('Evaluation output exists.')
    write_json(out,{'label_source':'human_reviewed','rows':len(df),'model_status':component.bundle['status'],
                    'algorithm':component.bundle.get('algorithm','logistic_regression'),'score_type':score_type,
                    'metrics':metrics(df[target],pred,p,component.classes)})


def main():
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='command',required=True)
    p=subs.add_parser('prepare');p.add_argument('--data',required=True);p.add_argument('--policy',default='draft_policy.json');p.add_argument('--output',required=True);p.add_argument('--review-size',type=int,default=1000)
    for name in ['train','compare']:
        p=subs.add_parser(name);p.add_argument('--data',required=True);p.add_argument('--labels',required=True);p.add_argument('--output',required=True);p.add_argument('--mode',choices=['draft','reviewed'],default='reviewed');p.add_argument('--acknowledge-draft-labels',action='store_true');p.add_argument('--test-from',default='2026-09-22');p.add_argument('--max-train-rows',type=int,default=30000)
        if name=='train':p.add_argument('--algorithm',choices=ALGORITHMS,default='logistic_regression')
    for name in ['predict','evaluate']:
        p=subs.add_parser(name);p.add_argument('--model',required=True);p.add_argument('--data',required=True);p.add_argument('--output',required=True)
        if name=='evaluate':p.add_argument('--labels',required=True)
    p=subs.add_parser('assess');p.add_argument('--model',required=True);p.add_argument('--request',required=True)
    args=parser.parse_args()
    try:globals()[args.command](args)
    except (ValueError,FileNotFoundError,FileExistsError) as e:parser.exit(2,f'Cannot continue: {e}\n')


if __name__=='__main__':main()
