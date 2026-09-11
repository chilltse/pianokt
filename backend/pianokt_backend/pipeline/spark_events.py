def run(raw,lake,checkpoint):
    import os
    from pyspark.sql import SparkSession, functions as F
    from pyspark.sql.types import StructType,StructField,StringType
    from delta import configure_spark_with_delta_pip
    from delta.tables import DeltaTable
    builder=(SparkSession.builder.appName('pianokt-events')
        .config('spark.sql.shuffle.partitions','2')
        .config('spark.hadoop.fs.gs.impl','com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem')
        .config('spark.hadoop.fs.AbstractFileSystem.gs.impl','com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS')
        .config('spark.sql.extensions','io.delta.sql.DeltaSparkSessionExtension')
        .config('spark.sql.catalog.spark_catalog','org.apache.spark.sql.delta.catalog.DeltaCatalog'))
    jars=os.getenv('PIANOKT_SPARK_JARS')
    spark=(builder.config('spark.jars',jars) if jars else configure_spark_with_delta_pip(builder)).getOrCreate()
    path=spark._jvm.org.apache.hadoop.fs.Path(raw+'/events')
    if not path.getFileSystem(spark._jsc.hadoopConfiguration()).exists(path): spark.stop(); return
    stream=(spark.readStream.format('text').option('maxFilesPerTrigger',100).load(raw+'/events')
        .select(F.col('value').alias('payload_json')).withColumn('event_id',F.sha2('payload_json',256)))
    schema=StructType([StructField('event_id',StringType()),StructField('event_type',StringType())])
    def upsert(df,path):
        if df.isEmpty(): return
        if DeltaTable.isDeltaTable(spark,path):
            DeltaTable.forPath(spark,path).alias('t').merge(df.alias('s'),'t.event_id=s.event_id').whenNotMatchedInsertAll().execute()
        else: df.write.format('delta').save(path)
    def process(batch,batch_id):
        batch=batch.dropDuplicates(['event_id']).persist()
        try:
            upsert(batch,lake+'/bronze/practice_events')
            parsed=batch.withColumn('parsed',F.from_json('payload_json',schema))
            valid=F.col('parsed.event_id').isNotNull() & F.col('parsed.event_type').isNotNull() & (F.length('parsed.event_id')>0) & (F.length('parsed.event_type')>0)
            upsert(parsed.filter(~valid).drop('parsed'),lake+'/quality/practice_events')
            upsert(parsed.filter(valid).select(F.col('parsed.event_id').alias('event_id'),'payload_json').dropDuplicates(['event_id']),lake+'/silver/practice_events')
        finally: batch.unpersist()
    try:
        stream.writeStream.foreachBatch(process).option('checkpointLocation',checkpoint+'/practice-events-v1').trigger(availableNow=True).start().awaitTermination()
    finally: spark.stop()
