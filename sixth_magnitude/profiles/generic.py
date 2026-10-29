"""A domain-neutral focus/context observation contract."""
from copy import deepcopy
from jsonschema import Draft202012Validator

VERSION = '1.0.0'
LABELS = {}
STATES = ['not_assessed', 'assessed', 'no_evidence_in_view', 'indeterminate', 'unassessable', 'not_applicable']

def scope(title):
    return {'type':'object','title':title,'additionalProperties':False,
        'required':['assessment_state','findings','note'],
        'properties':{
            'assessment_state':{'type':'string','enum':STATES,'default':'not_assessed'},
            'findings':{'type':'array','maxItems':20,'uniqueItems':True,'default':[],
                        'items':{'type':'string','minLength':1,'maxLength':160}},
            'note':{'type':['string','null'],'maxLength':1000,'default':None}}}

SCHEMA = {'$schema':'https://json-schema.org/draft/2020-12/schema',
    '$id':'urn:sixth-magnitude:generic:1.0.0','type':'object','additionalProperties':False,
    'title':'Focus and context inspection',
    'required':['schema_version','label','focus','context'],
    'properties':{
        'schema_version':{'type':'string','const':VERSION,'default':VERSION},
        'label':{'type':'string','minLength':1,'maxLength':160,'default':''},
        'focus':scope('Inside the selected region'),
        'context':scope('Surrounding window, excluding the selected region')}}
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA)

def defaults():
    empty={'assessment_state':'not_assessed','findings':[],'note':None}
    return {'schema_version':VERSION,'label':'','focus':deepcopy(empty),'context':deepcopy(empty)}

def validate(observation):
    errors=sorted(VALIDATOR.iter_errors(observation),key=lambda e:str(list(e.path)))
    if errors:
        raise ValueError(f'{".".join(map(str,errors[0].path))}: {errors[0].message}')
    if not observation['label'].strip():
        raise ValueError('A nonempty label is required')
    for name in ('focus','context'):
        item=observation[name]
        if item['findings'] and item['assessment_state'] not in ('assessed','indeterminate'):
            raise ValueError(f'{name}: findings require assessed or indeterminate state')
    return observation
