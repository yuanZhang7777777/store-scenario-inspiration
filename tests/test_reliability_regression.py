"""Regression tests. In a checkout, imports the real patched project modules.

The supplied run_offline_tests.py explicitly selects the payload-only harness;
that mode is NOT an end-to-end test or a substitute for the original pytest suite.
All model calls are mocked at the network/provider boundary.
"""
from __future__ import annotations
from concurrent.futures import Future
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch
import numpy as np

if os.environ.get('SSI_FIX_PAYLOAD_ONLY') == '1':
    from offline_modules import (analysis, vectors, providers, rerank, exports, rel,
                                 jobs, retrieval, stores, Params, WS)
else:
    from store_scenario_inspiration import reliability as rel
    from store_scenario_inspiration.pipeline import analysis
    from store_scenario_inspiration.catalog import bilingual_vectors as vectors
    from store_scenario_inspiration.app import rerank_providers as providers
    from store_scenario_inspiration.app import (rerank, export as exports,
                                                jobs, retrieval, stores)
    from store_scenario_inspiration.app.params import SearchParams as Params
    from store_scenario_inspiration.app.stores import Workspace as WS


def source():
    return {'scenes': [{'scene_name':'露营', 'products':[
        {'product_cn':'折叠椅','product_en':'folding chair'},
        {'product_cn':'帐篷','product_en':'tent'}]}]}


def expanded():
    return analysis._original_expansions(source())


def rows():
    return [{'main_sku':'A','rank':1,'country_available':True},
            {'main_sku':'B','rank':2,'country_available':True}]


class ExpansionTests(unittest.TestCase):
    def test_non_array_is_rejected_not_split_into_letters(self):
        for value in ('folding chair', None, 7, {}, True):
            with self.subTest(value=value):
                data=expanded(); data['scenes'][0]['products'][0]['expanded_en']=value
                with self.assertRaises(ValueError): analysis.normalize_expansions(data)

    def test_non_string_terms_rejected(self):
        for value in ([1], [None], [{}], [False]):
            with self.subTest(value=value):
                data=expanded(); data['scenes'][0]['products'][0]['expanded_en']=value
                with self.assertRaises(ValueError): analysis.normalize_expansions(data)

    def test_empty_and_duplicate_terms_normalized(self):
        data=expanded(); data['scenes'][0]['products'][0]['expanded_en']=[' chair ','','chair','seat']
        result=analysis.normalize_expansions(data, expansion_terms=1)
        self.assertEqual(result['scenes'][0]['products'][0]['expanded_en'], ['chair'])

    def test_zero_expansions_keeps_original_product(self):
        with patch.object(analysis,'_ask',return_value=(expanded(),{})):
            value,_=analysis.analyze_expansions(source(),'fixture',expansion_terms=0)
        self.assertEqual(len(value['scenes'][0]['products']),2)
        self.assertEqual(value['scenes'][0]['products'][1]['canonical_en'],'tent')

    def test_missing_role_falls_back_without_suppressing_good_role(self):
        data=expanded(); data['scenes'][0]['products'].pop()
        data['scenes'][0]['products'][0]['expanded_en']=['camp chair']
        with patch.object(analysis,'_ask',return_value=(data,{'usage':{'total_tokens':17}})):
            value,receipt=analysis.analyze_expansions(source(),'fixture')
        self.assertEqual([x['product_en'] for x in value['scenes'][0]['products']],['folding chair','tent'])
        self.assertEqual(value['scenes'][0]['products'][0]['expanded_en'],['camp chair'])
        self.assertEqual(receipt['expansion_fallback']['count'],1)
        self.assertEqual(receipt['usage']['total_tokens'],17)

    def test_bad_role_only_uses_original_terms(self):
        data=expanded(); data['scenes'][0]['products'][0]['expanded_en']='abc'
        with patch.object(analysis,'_ask',return_value=(data,{})):
            value,receipt=analysis.analyze_expansions(source(),'fixture')
        self.assertEqual(value['scenes'][0]['products'][0]['expanded_en'],[])
        self.assertEqual(receipt['expansion_fallback']['count'],1)

    def test_missing_scene_uses_all_originals(self):
        with patch.object(analysis,'_ask',return_value=({'scenes':[]},{})):
            value,receipt=analysis.analyze_expansions(source(),'fixture')
        self.assertEqual(value,expanded())
        self.assertEqual(receipt['expansion_fallback']['count'],2)

    def test_timeout_makes_one_attempt_and_continues_with_originals(self):
        with patch.object(analysis,'_ask',side_effect=TimeoutError('secret must not leak')) as call:
            value,receipt=analysis.analyze_expansions(source(),'fixture')
        self.assertEqual(call.call_count,1)
        self.assertEqual(value,expanded())
        self.assertNotIn('secret',json.dumps(receipt))

    def test_duplicates_or_renames_do_not_replace_original_identity(self):
        for duplicate in (True,False):
            with self.subTest(duplicate=duplicate):
                data=expanded()
                if duplicate: data['scenes'][0]['products'].append(copy.deepcopy(data['scenes'][0]['products'][0]))
                else: data['scenes'][0]['products'][0]['product_cn']='不在输入中的商品'
                with patch.object(analysis,'_ask',return_value=(data,{})):
                    value,receipt=analysis.analyze_expansions(source(),'fixture')
                self.assertEqual(value['scenes'][0]['products'][0]['product_cn'],'折叠椅')
                self.assertEqual(receipt['expansion_fallback']['count'],1)

    def test_extra_model_products_never_enter_retrieval(self):
        data=expanded(); extra=copy.deepcopy(data['scenes'][0]['products'][0]); extra['product_cn']='未知商品'
        data['scenes'][0]['products'].append(extra)
        with patch.object(analysis,'_ask',return_value=(data,{})):
            value,_=analysis.analyze_expansions(source(),'fixture')
        self.assertEqual(len(value['scenes'][0]['products']),2)

    def test_invalid_source_is_not_fabricated(self):
        with patch.object(analysis,'_ask') as call:
            with self.assertRaises(ValueError): analysis.analyze_expansions({'scenes':[]},'fixture')
        call.assert_not_called()


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        vectors.load_vector_index.cache_clear()
        self.addCleanup(vectors.load_vector_index.cache_clear)

    def database(self, path, names, wal=False):
        conn=sqlite3.connect(path)
        if wal: conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE IF NOT EXISTS embeddings(language TEXT, model_key TEXT, main_sku TEXT, vector BLOB)')
        for name in names:
            for lang in ('cn','en'):
                v=np.zeros(1024,dtype=np.float32); v[0]=1
                conn.execute('INSERT INTO embeddings VALUES(?,?,?,?)',(lang,'fixture@v1',name,v.tobytes()))
        conn.commit()
        return conn

    def test_same_path_update_invalidates_cached_vectors(self):
        path=self.base/'vectors.db'; self.database(path,['A']).close()
        first=vectors.load_vector_index(str(path))
        self.database(path,['B']).close()
        second=vectors.load_vector_index(str(path))
        self.assertEqual(second.skus,('A','B')); self.assertIsNot(first,second)

    def test_wal_update_invalidates_cached_vectors(self):
        path=self.base/'vectors.db'; conn=self.database(path,['A'],wal=True)
        self.addCleanup(conn.close)
        first=vectors.load_vector_index(str(path))
        self.database(path,['B']).close()
        second=vectors.load_vector_index(str(path))
        self.assertEqual(second.skus,('A','B')); self.assertIsNot(first,second)

    def test_unchanged_file_reuses_cached_vectors(self):
        path=self.base/'vectors.db'; self.database(path,['A']).close()
        self.assertIs(vectors.load_vector_index(str(path)),vectors.load_vector_index(str(path)))

    def test_atomic_replacement_invalidates_cache(self):
        path=self.base/'vectors.db'; self.database(path,['A']).close()
        vectors.load_vector_index(str(path))
        new=self.base/'next.db'; self.database(new,['B']).close(); os.replace(new,path)
        self.assertEqual(vectors.load_vector_index(str(path)).skus,('B',))

    def test_removed_source_never_returns_old_cached_value(self):
        path=self.base/'vectors.db'; self.database(path,['A']).close()
        vectors.load_vector_index(str(path)); path.unlink()
        with self.assertRaises(FileNotFoundError): vectors.load_vector_index(str(path))

    def test_catalogue_style_reader_has_same_invalidation(self):
        path=self.base/'catalogue.json'; path.write_text('A'); calls=[]
        @rel.revision_cached
        def read(filename): calls.append(filename); return Path(filename).read_text()
        self.assertEqual(read(str(path)),'A'); self.assertEqual(read(str(path)),'A')
        path.write_text('B'); self.assertEqual(read(str(path)),'B'); self.assertEqual(len(calls),2)

    def test_continuously_changing_file_stops_instead_of_publishing(self):
        path=self.base/'changing'; path.write_text('x'); calls=[]
        @rel.revision_cached
        def read(filename):
            calls.append(filename); Path(filename).write_text('x'* (len(calls)+1)); return 'unsafe'
        with self.assertRaises(rel.DataChangedError): read(str(path))
        self.assertEqual(len(calls),2)

    def test_atomic_json_preserves_old_content_on_serialization_error(self):
        path=self.base/'value.json'; rel.atomic_json(path,{'old':1})
        with self.assertRaises(ValueError): rel.atomic_json(path,{'bad':float('nan')})
        self.assertEqual(json.loads(path.read_text()),{'old':1})
        self.assertEqual([x.name for x in self.base.iterdir()],['value.json'])


class VerdictTests(unittest.TestCase):
    def test_empty_invalid_json_objects_are_not_all_related(self):
        for body in ('{}','[]','null','{"unrelated":null}','{"unrelated":{}}','{"unrelated":"A"}','not json'):
            with self.subTest(body=body):
                with self.assertRaises(RuntimeError): providers.read_deepseek_verdicts(body,['A'])

    def test_valid_empty_array_is_distinguished_from_missing_field(self):
        self.assertEqual(providers.read_deepseek_verdicts('{"unrelated":[]}', ['A'])['A']['verdict'],'related')

    def test_invalid_confidence_cannot_delete_candidates(self):
        for value in (None,True,False,-.1,1.2,float('nan'),float('inf'),'0.9'):
            with self.subTest(value=value):
                verdict=providers.read_deepseek_verdicts(json.dumps({'unrelated':[{'main_sku':'A','confidence':value}]}), ['A'])['A']
                self.assertIsNone(verdict['probability'])
                self.assertFalse(rerank._is_confidently_unrelated(verdict,.5))

    def test_foreign_duplicate_or_malformed_sku_rejects_slice(self):
        for value in ([{}],[None],[{'main_sku':[]}],[{'main_sku':'X'}],[{'main_sku':'A'},{'main_sku':'A'}]):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError): providers.read_deepseek_verdicts(json.dumps({'unrelated':value}),['A'])

    def test_typesafe_bad_answer_types_remain_unknown(self):
        for value in (None,[],5,{'A':[]},{'A':{'type':'choice','choice':[]}}):
            with self.subTest(value=value):
                self.assertIsNone(providers._read_typesafe_answers(value,['A'])['A']['verdict'])

    def test_mark_only_then_off_restores_original_order(self):
        verdicts={'A':{'verdict':'unrelated','probability':.95},'B':{'verdict':'related','probability':.9}}
        kept,dropped=rerank.apply_verdicts(rows(),verdicts,cutoff=.7,drop=False)
        self.assertEqual([x['main_sku'] for x in kept],['B','A'])
        payload={'scenes':[{'candidates':kept,'dropped':dropped}]}
        self.assertEqual(rerank.strip_verdicts(payload),0)
        self.assertEqual([x['main_sku'] for x in payload['scenes'][0]['candidates']],['A','B'])

    def test_drop_then_off_restores_and_is_idempotent(self):
        verdicts={'A':{'verdict':'unrelated','probability':.95},'B':{'verdict':'related','probability':.9}}
        kept,dropped=rerank.apply_verdicts(rows(),verdicts,cutoff=.7,drop=True)
        payload={'scenes':[{'candidates':kept,'dropped':dropped}], 'rerank':{}}
        self.assertEqual(rerank.strip_verdicts(payload),1)
        before=copy.deepcopy(payload); self.assertEqual(rerank.strip_verdicts(payload),0)
        self.assertEqual(before,payload)

    def test_failed_scene_does_not_remove_candidates_or_other_scene_verdicts(self):
        roles=[{'scene_name':'bad','candidates':rows()},{'scene_name':'good','candidates':rows()}]
        def ask(name, roles):
            if name=='bad': raise RuntimeError('offline')
            return {sku:{'verdict':'related','probability':.8} for sku in ('A','B')},{}
        result,summary=rerank.rerank_scenes(roles,ask=ask,cutoff=.5,drop=True)
        self.assertEqual(summary['failed'],1); self.assertEqual(summary['answered'],2)
        self.assertEqual(len(result[0]['candidates']),2)

    def test_partial_deepseek_slice_preserves_good_answers(self):
        roles=[{'scene_name':'s','candidates':[{'main_sku':x,'standard_name_en':x} for x in ('A','B','C')]}]
        responses=[{'choices':[{'message':{'content':'{"unrelated":[]}'},'finish_reason':'stop'}]},
                   {'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]}]
        with patch.object(providers,'DEEPSEEK_SLICE',2), patch.object(providers,'_post',side_effect=responses):
            result,usage=providers.ask_deepseek('s',roles,api_key='fixture')
        self.assertEqual(result['A']['verdict'],'related'); self.assertIsNone(result['C']['verdict'])
        self.assertEqual(usage['_failed_slices'],1)

    def test_truncated_response_is_not_cached_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            response={'choices':[{'message':{'content':'{"unrelated":[]}'},'finish_reason':'length'}]}
            with patch.object(providers,'_post',return_value=response):
                result,usage=providers.ask_deepseek('s',[{'candidates':[{'main_sku':'A','standard_name_en':'A'}]}],
                                                    api_key='fixture',cache_dir=Path(directory))
            self.assertIsNone(result['A']['verdict']); self.assertEqual(list(Path(directory).glob('*.json')),[])

    def test_corrupt_cache_is_replaced_after_one_valid_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory); roles=[{'candidates':[{'main_sku':'A','standard_name_en':'A'}]}]
            response={'choices':[{'message':{'content':'{"unrelated":[]}'},'finish_reason':'stop'}]}
            with patch.object(providers,'_post',return_value=response):
                providers.ask_deepseek('s',roles,api_key='fixture',cache_dir=path)
            cache=next(path.glob('*.json')); cache.write_text('broken')
            with patch.object(providers,'_post',return_value=response) as call:
                result,_=providers.ask_deepseek('s',roles,api_key='fixture',cache_dir=path)
            self.assertEqual(call.call_count,1); self.assertEqual(result['A']['verdict'],'related')
            self.assertIn('content',json.loads(cache.read_text()))


    def test_deepseek_malformed_envelope_keeps_slice_unknown(self):
        roles=[{'candidates':[{'main_sku':'A','standard_name_en':'A'}]}]
        for body in ([],None,{}, {'choices':[None]}, {'choices':[{'message':[]}]}):
            with self.subTest(body=body), patch.object(providers,'_post',return_value=body):
                result,usage=providers.ask_deepseek('s',roles,api_key='fixture')
                self.assertIsNone(result['A']['verdict'])
                self.assertEqual(usage['_failed_slices'],1)

    def test_typesafe_missing_answers_are_never_reused_as_complete(self):
        """What matters is that a partial answer never stands in for the real one
        on a later run: it is asked again, and the SKU stays unanswered until
        something actually answers it. (The bytes are still written to the cache,
        because that is what was asked and what came back.)"""
        roles=[{'candidates':[{'main_sku':'A','standard_name_en':'A'}]}]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(providers,'_post',return_value={'answers':{}}) as call:
                result,usage=providers.ask_typesafe('s',roles,api_key='fixture',cache_dir=Path(directory))
                again,_=providers.ask_typesafe('s',roles,api_key='fixture',cache_dir=Path(directory))
            self.assertIsNone(result['A']['verdict'])
            self.assertIsNone(again['A']['verdict'])
            self.assertEqual(usage['_failed_slices'],1)
            self.assertEqual(call.call_count,2)

    def test_typesafe_partial_cached_answer_is_requeried_once(self):
        roles=[{'candidates':[{'main_sku':'A','standard_name_en':'A'}]}]
        valid={'answers':{'A':{'type':'choice','choice':'related','probabilities':{'related':.9}}}}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(providers,'_post',return_value=valid):
                providers.ask_typesafe('s',roles,api_key='fixture',cache_dir=Path(directory))
            cached=next(Path(directory).glob('*.json'))
            cached.write_text('{"answers":{}}')
            with patch.object(providers,'_post',return_value=valid) as call:
                result,_=providers.ask_typesafe('s',roles,api_key='fixture',cache_dir=Path(directory))
            self.assertEqual(call.call_count,1)
            self.assertEqual(result['A']['verdict'],'related')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ws=WS(Path(self.tmp.name)); self.base=self.ws.dir('s'); self.base.mkdir(parents=True)
        rel.atomic_json(self.ws.path('s','store.json'),{'store_name':'fixture','country':'PH','images':[]})
        rel.atomic_json(self.base/'sample_store.json',{'store':{'store_name':'fixture'},'business_context':{}})
        rel.atomic_json(self.base/'clues.json',{'entries':[{'name':'折叠椅'}]})
        rel.atomic_json(self.base/'analysis_input.json',{'store':{'store_name':'fixture'},
                                                         'observed_product_clues':[{'name':'折叠椅'}]})
        rel.atomic_json(self.base/'deepseek_scenes.json',{'model':'deepseek-flash','scenes':[
            {'scene_name':'A','audience':'a','user_need':'n','evidence':'e'},
            {'scene_name':'B','audience':'a','user_need':'n','evidence':'e'}]})
        self.params=Params(); self.settings=types.SimpleNamespace(api_key='fixture',typesafe_key='',data_dir=Path(self.tmp.name))

    def product(self, source, scene, key, **kwargs):
        return {'products':[{'product_cn':scene['scene_name']+'椅','product_en':scene['scene_name']+' chair','purpose':'fixture'}]}, {'usage':{'total_tokens':3}}

    def generate(self):
        with patch.object(jobs,'analyze_scene_products',side_effect=self.product):
            jobs.run_products(self.ws,self.settings,'s',self.params)

    def test_new_batch_does_not_glob_old_scene_files(self):
        self.generate()
        rel.atomic_json(self.base/'products'/'99-A.json',{'scene_name':'A','products':[{'product_cn':'旧错误商品'}]})
        frames=jobs._product_frames(self.base)
        self.assertEqual([frame['products'][0]['product_cn'] for frame in frames],['A椅','B椅'])

    def test_reordered_scenes_read_only_new_manifest(self):
        self.generate(); skeleton=json.loads((self.base/'deepseek_scenes.json').read_text())
        skeleton['scenes'].reverse(); rel.atomic_json(self.base/'deepseek_scenes.json',skeleton)
        self.generate()
        self.assertEqual([frame['scene_name'] for frame in jobs._product_frames(self.base)],['B','A'])

    def test_source_change_invalidates_manifest(self):
        self.generate(); rel.atomic_json(self.base/'analysis_input.json',{'changed':True})
        with self.assertRaises(RuntimeError): jobs._product_frames(self.base)

    def test_product_file_tampering_is_detected(self):
        self.generate(); manifest=json.loads((self.base/'products/manifest.json').read_text())
        path=self.base/'products'/manifest['files'][0]['path']
        rel.atomic_json(path,{'scene_name':'A','products':[]})
        with self.assertRaises(RuntimeError): jobs._product_frames(self.base)

    def test_failed_scene_can_resume_without_repeating_successful_scene(self):
        calls=[]
        def first(source,scene,key,**kwargs):
            calls.append(scene['scene_name'])
            if scene['scene_name']=='B': raise RuntimeError('offline')
            return self.product(source,scene,key,**kwargs)
        with patch.object(jobs,'analyze_scene_products',side_effect=first):
            with self.assertRaises(RuntimeError): jobs.run_products(self.ws,self.settings,'s',self.params)
        with self.assertRaises(RuntimeError): jobs._product_frames(self.base)
        with patch.object(jobs,'analyze_scene_products',side_effect=lambda s,c,k,**kw:(calls.append(c['scene_name']) or self.product(s,c,k,**kw))):
            jobs.run_products(self.ws,self.settings,'s',self.params)
        self.assertEqual(calls.count('A'),1); self.assertEqual(calls.count('B'),2)
        self.assertEqual(len(jobs._product_frames(self.base)),2)

    def test_partial_report_has_safe_types_not_fabricated_advice(self):
        self.generate()
        value=json.loads((self.base/'deepseek_analysis.json').read_text())
        self.assertEqual(value['analysis_status'],'partial')
        self.assertEqual(value['manager_summary']['recommended_actions'],[])
        self.assertEqual(value['future_product_structure']['priority_order'],[])

    def test_synthesis_failure_still_allows_expansion_from_products(self):
        self.generate()
        with patch.object(jobs,'analyze_synthesis',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): jobs.run_synthesis(self.ws,self.settings,'s',self.params)
        self.assertEqual(json.loads((self.base/'deepseek_analysis.json').read_text())['analysis_status'],'partial')
        with patch.object(analysis,'_ask',side_effect=RuntimeError('offline')):
            jobs.run_expand(self.ws,self.settings,'s',self.params)
        self.assertEqual(len(json.loads((self.base/'expansions.json').read_text())['scenes']),2)

    def test_legacy_uses_exact_index_not_stale_same_name(self):
        for index,name in enumerate(('A','B')):
            rel.atomic_json(self.base/'products'/f'{index:02d}-{name}.json',{'scene_name':name,'products':[{'product_cn':'new'}]})
        rel.atomic_json(self.base/'products'/'02-A.json',{'scene_name':'A','products':[{'product_cn':'old'}]})
        self.assertEqual(jobs._product_frames(self.base)[0]['products'][0]['product_cn'],'new')

    def test_rerank_outer_failure_persists_restored_candidates(self):
        payload={'scenes':[{'scene_name':'s','candidates':[{'main_sku':'B','rank':1,'recall_rank':2,'rerank':'related'}],
                           'dropped':[{'main_sku':'A','rank':1,'recall_rank':1,'rerank':'unrelated'}]}],'rerank':{'old':True}}
        rel.atomic_json(self.base/'retrieval.json',payload); rel.atomic_json(self.base/'rerank.json',{'old':True})
        params=Params(rerank_provider='deepseek')
        with patch.object(jobs,'rerank_store',side_effect=RuntimeError('offline')):
            jobs.run_rerank(self.ws,self.settings,'s',params)
        actual=json.loads((self.base/'retrieval.json').read_text())
        self.assertEqual([x['main_sku'] for x in actual['scenes'][0]['candidates']],['A','B'])
        self.assertNotIn('rerank',actual); self.assertFalse((self.base/'rerank.json').exists())

    def manager(self):
        manager=jobs.JobManager(self.ws,self.settings)
        self.addCleanup(lambda:manager._pool.shutdown(wait=True,cancel_futures=True))
        return manager

    def test_duplicate_start_reuses_queued_job(self):
        manager=self.manager(); future=Future()
        with patch.object(manager._pool,'submit',return_value=future):
            first=manager.start('s',('retrieval',),self.params)
            second=manager.start('s',('retrieval',),self.params)
        self.assertEqual(first['id'],second['id']); future.cancel()

    def test_conflicting_duplicate_does_not_overwrite_parameters(self):
        manager=self.manager(); future=Future()
        with patch.object(manager._pool,'submit',return_value=future):
            manager.start('s',('retrieval',),self.params)
            with self.assertRaises(ValueError): manager.start('s',('retrieval',),Params(recall_limit=50))
        self.assertEqual(jobs.load_params(self.base/'params.json').recall_limit,self.params.recall_limit)
        future.cancel()

    def test_queued_job_uses_frozen_parameters(self):
        manager=self.manager(); future=Future(); observed=[]
        with patch.object(manager._pool,'submit',return_value=future):
            answer=manager.start('s',('retrieval',),self.params)
        jobs.save_params(self.base/'params.json',Params(recall_limit=50))
        with patch.dict(jobs.STAGE_RUNNERS,{'retrieval':lambda w,s,i,p:observed.append(p.recall_limit) or 'ok'}):
            manager._run(manager._jobs[answer['id']])
        future.set_result(None)
        self.assertEqual(observed,[30])

    def test_synthesis_is_optional_but_matching_remains_part_of_one_job(self):
        manager=self.manager(); future=Future(); steps=[]
        with patch.object(manager._pool,'submit',return_value=future):
            answer=manager.start('s',('synthesis','expand','retrieval'),self.params)
        def fail(*a): steps.append('synthesis'); raise RuntimeError('offline')
        with patch.dict(jobs.STAGE_RUNNERS,{'synthesis':fail, 'expand':lambda *a:steps.append('expand') or 'ok',
                                         'retrieval':lambda *a:steps.append('retrieval') or 'ok'}):
            manager._run(manager._jobs[answer['id']])
        future.set_result(None)
        self.assertEqual(steps,['synthesis','expand','retrieval'])
        self.assertEqual(manager.snapshot(answer['id'])['status'],'ready')
        self.assertEqual(manager._jobs[answer['id']].stages[0].status,'failed')

    def test_critical_stage_failure_stops_dependent_stages(self):
        manager=self.manager(); future=Future(); steps=[]
        with patch.object(manager._pool,'submit',return_value=future):
            answer=manager.start('s',('products','retrieval'),self.params)
        def fail(*a): raise RuntimeError('offline')
        with patch.dict(jobs.STAGE_RUNNERS,{'products':fail,'retrieval':lambda *a:steps.append('bad')}):
            manager._run(manager._jobs[answer['id']])
        future.set_result(None)
        self.assertEqual(steps,[]); self.assertEqual(manager.snapshot(answer['id'])['status'],'failed')

    def test_queued_cancellation_releases_store_lease(self):
        manager=self.manager(); future=Future()
        with patch.object(manager._pool,'submit',return_value=future): manager.start('s',('retrieval',),self.params)
        future.cancel(); lease=rel.StoreLease(self.base/'.pipeline.lock'); lease.close()

    def test_two_managers_cannot_run_same_store_at_once(self):
        one=self.manager(); two=self.manager(); future=Future()
        with patch.object(one._pool,'submit',return_value=future): one.start('s',('retrieval',),self.params)
        with self.assertRaises(ValueError): two.start('s',('retrieval',),self.params)
        future.cancel()

    def test_submit_failure_releases_store_lease(self):
        manager=self.manager()
        with patch.object(manager._pool,'submit',side_effect=RuntimeError('closed')):
            with self.assertRaises(RuntimeError): manager.start('s',('retrieval',),self.params)
        lease=rel.StoreLease(self.base/'.pipeline.lock'); lease.close()


class StockTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name); self.stock=self.base/'stock.fixture'; self.stock.write_text('old')

    def note(self, result):
        return dict(exports.notes_for(retrieval=result,params={},store={},store_id='s',stock_path=self.stock,exported=1))

    def test_export_stays_bound_to_original_snapshot_after_file_update(self):
        result={'inventory':'available','inventory_snapshot':rel.stock_snapshot(self.stock)}
        before=self.note(result); self.stock.write_text('new inventory with different bytes')
        after=self.note(result)
        self.assertEqual(before['库存快照时间'],after['库存快照时间'])
        self.assertEqual(before['库存快照 SHA256'],after['库存快照 SHA256'])

    def test_legacy_export_does_not_backfill_current_file_timestamp(self):
        note=self.note({'inventory':'available'})
        self.assertIn('未记录',note['库存快照时间']); self.assertEqual(note['库存快照 SHA256'],'未记录')

    def test_deleted_inventory_file_does_not_erase_saved_provenance(self):
        result={'inventory':'available','inventory_snapshot':rel.stock_snapshot(self.stock)}
        self.stock.unlink(); self.assertEqual(self.note(result)['库存快照文件'],'stock.fixture')

    def retrieve(self, *, only=False, stock=None, failure=None, mutate=False):
        db=self.base/'assets.fixture'; db.write_text('catalogue')
        vec=self.base/'vectors.fixture'; vec.write_text('vectors')
        params=Params(stock_filter='in_stock' if only else 'all')
        def ranks(*args):
            if mutate: db.write_text('modified during retrieval')
            return []
        inventory=patch.object(retrieval,'_inventory',side_effect=failure) if failure else patch.object(retrieval,'_inventory',return_value=('PH','available' if stock is not None else 'unavailable',stock,stock))
        with inventory, patch.object(retrieval,'load_products',return_value={}), patch.object(retrieval,'load_vector_index',return_value=object()), patch.object(retrieval,'load_encoder',return_value=object()), patch.object(retrieval,'_rankings',side_effect=ranks), patch.object(retrieval,'fuse_rankings',return_value=rows()), patch.object(retrieval,'build_fts',side_effect=lambda *a:sqlite3.connect(':memory:')):
            # The candidates are the document every caller here reads; the per-child
            # stock that comes back beside it is the export sheet's business.
            return retrieval.retrieve_store(asset_db=db,vector_cache=vec,model_cache=self.base,stock_path=self.stock,
                                            country='PH',products=[{'scene_name':'s','product_cn':'椅','product_en':'chair'}],params=params)[0]

    def test_unknown_stock_never_satisfies_in_stock_filter(self):
        with self.assertRaises(RuntimeError): self.retrieve(only=True)

    def test_corrupt_inventory_keeps_unconfirmed_semantic_candidates(self):
        result=self.retrieve(failure=ValueError('bad headers'))
        self.assertEqual(len(result['scenes'][0]['candidates']),2)
        self.assertTrue(all(x['country_available'] is None for x in result['scenes'][0]['candidates']))
        self.assertIsNone(result['inventory_snapshot']); self.assertTrue(result['warnings'])

    def test_data_change_during_retrieval_blocks_mixed_result(self):
        with self.assertRaises(rel.DataChangedError): self.retrieve(stock={'A':1},mutate=True)

    def test_empty_in_stock_window_is_not_called_empty_entire_catalogue(self):
        result=self.retrieve(only=True,stock={})
        self.assertEqual(result['scenes'][0]['match_status'],'no_stock_in_recalled_candidates')
        self.assertEqual(result['scenes'][0]['candidate_count_before_stock'],2)

    def test_positive_inventory_preserves_policy_and_source(self):
        result=self.retrieve(only=True,stock={'B':3})
        self.assertEqual([x['main_sku'] for x in result['scenes'][0]['candidates']],['B'])
        self.assertEqual(result['scenes'][0]['candidates'][0]['rank'],1)
        self.assertEqual(result['inventory_snapshot']['filename'],'stock.fixture')


if __name__=='__main__': unittest.main(verbosity=2)
