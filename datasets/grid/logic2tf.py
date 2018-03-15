import sys
import time
import tensorflow as tf
import numpy as np
import random

EDGE_WEIGHT = 0.2
EDGE_FRAC = 1.0 # fraction to keep
TORUS = True    # wrap edges, so j,1 --> j,n+1 and etc

#
# simple demo of adding some numeric learning on top of the logic part
# 
# 
#

from tensorlog import simple,program,declare,dbschema
import expt


def setup_tlog(maxD,factFile,trainFile,testFile):
  tlog = simple.Compiler(db=factFile,prog="grid.ppr")
  tlog.prog.db.markAsParameter('edge',2)
  tlog.prog.maxDepth = maxD
  print 'loading trainData,testData from',trainFile,testFile
  trainData = tlog.load_small_dataset(trainFile)
  testData = tlog.load_small_dataset(testFile)
  return (tlog,trainData,testData)

def trainAndTest(tlog,trainData,testData,epochs,corner='hard'):
  mode = 'path/io'
  predicted_y = tlog.inference(mode)
  actual_y = tlog.target_output_placeholder(mode)
  correct_predictions = tf.equal(tf.argmax(actual_y,1), tf.argmax(predicted_y,1))
  accuracy = tf.reduce_mean(tf.cast(correct_predictions, tf.float32))

  if corner=='soft':
    # adding NULL_ENTITY_NAME cost doesn't seem to help...
    x1 = tlog.db.matEncoding[('x1',1)] + 10000*tlog.db.onehot(dbschema.NULL_ENTITY_NAME)
    x2 = tlog.db.matEncoding[('x2',1)] + 10000*tlog.db.onehot(dbschema.NULL_ENTITY_NAME)
    x1 = x1.todense()
    x2 = x2.todense()
    #print 'symbols',map(lambda i:tlog.db.schema.getSymbol('__THING__',i),[0,1,2,3,4,5,6])
    #print 'x1+x2-2',x1+x2-2
    loss = tf.reduce_sum(tf.multiply(predicted_y, (x1+x2-2)))
  else: 
    loss = tlog.loss(mode)

  #optimizer = tf.train.AdagradOptimizer(0.5)
  optimizer = tf.train.AdamOptimizer(0.1)
  train_step = optimizer.minimize(loss)

  session = tf.Session()
  session.run(tf.global_variables_initializer())

  (ux,uy) = testData[mode]
  test_fd = {tlog.input_placeholder_name(mode):ux, tlog.target_output_placeholder_name(mode):uy}
  acc = session.run(accuracy, feed_dict=test_fd)
  print corner,'training: initial test acc',acc

  (tx,ty) = trainData[mode]
  if corner=='hard':
    train_fd = {tlog.input_placeholder_name(mode):tx, tlog.target_output_placeholder_name(mode):ty}
  else:
    train_fd = {tlog.input_placeholder_name(mode):tx}  # not using labels

  def show_test_results():
    if corner=='soft':
      test_preds = session.run(tf.argmax(predicted_y,1), feed_dict=test_fd)    
      print 'test best symbols are',map(lambda i:tlog.db.schema.getSymbol('__THING__',i),test_preds)
      if False:
        test_scores = session.run(predicted_y, feed_dict=test_fd)    
        print 'test scores are',test_scores
        weighted_predictions = session.run(tf.multiply(predicted_y, (x1+x2-2)), feed_dict=test_fd)    
        print 'test weighted_predictions',weighted_predictions
        test_loss = session.run(loss,feed_dict=test_fd)    
        print 'test loss',test_loss

  show_test_results()

  t0 = time.time()
  print 'epoch',
  for i in range(epochs):
    print i+1,
    session.run(train_step, feed_dict=train_fd)
    if (i+1)%3==0:
      test_fd = {tlog.input_placeholder_name(mode):ux, tlog.target_output_placeholder_name(mode):uy}
      train_loss = session.run(loss, feed_dict=train_fd)
      if corner=='hard':
        train_acc = session.run(accuracy, feed_dict=train_fd)
      test_loss = session.run(loss, feed_dict=test_fd)
      test_acc = session.run(accuracy, feed_dict=test_fd)
      print 'train loss',train_loss,
      if corner=='hard':
        print 'acc',train_acc,
      print 'test loss',test_loss,'acc',test_acc
      if train_loss < 10.0:
        break
      print 'epoch',
  print 'done'
  print 'learning takes',time.time()-t0,'sec'

  acc = session.run(accuracy, feed_dict=test_fd)
  print 'test acc',acc

  show_test_results()

  tlog.set_all_db_params_to_learned_values(session)
  tlog.serialize_db('learned.db')
  return acc

def nodeName(i,j):
    return '%d,%d' % (i,j)

def genInputs(n):
    #generate grid

    stem = 'inputs/g%d' % n

    factFile = stem+'_logic.cfacts'
    trainFile = stem+'_logic-train.exam'
    testFile = stem+'_logic-test.exam'
    rnd = random.Random()
    rnd.seed(0)
    # generate the facts
    with open(factFile,'w') as fp:
      def connect(i1,j1,i2,j2):
        fp.write('edge\t%s\t%s\t%f\n' % (nodeName(i1,j1),nodeName(i2,j2),EDGE_WEIGHT+rnd.random()*0.02-0.01))
      #edges
      for i in range(1,n+1):
        for j in range(1,n+1):
          for di in [-1,0,+1]:
            for dj in [-1,0,+1]:
              if (1 <= i+di <= n) and (1 <= j+dj <= n) and rnd.random() <= EDGE_FRAC:
                connect(i,j,i+di,j+dj)
      if TORUS:
        for k in range(1,n):
          connect(k,1,k,n)
          connect(k,n,k,1)
          connect(1,k,n,k)
          connect(n,k,1,k)
      # x1,x2 are row,col positions
      for i in range(1,n+1):
        for j in range(1,n+1):
          v = nodeName(i,j)
          x1 = i
          fp.write('\t'.join(['x1',v,str(x1)]) + '\n')
      for i in range(1,n+1):
        for j in range(1,n+1):
          v = nodeName(i,j)
          x2 = j
          fp.write('\t'.join(['x2',v,str(x2)]) + '\n')
    # data
    with open(trainFile,'w') as fpTrain,open(testFile,'w') as fpTest:
      r = random.Random()
      for i in range(1,n+1):
        for j in range(1,n+1):
          ti,tj = 1,1
          x = nodeName(i,j)
          y = nodeName(ti,tj)
          fp = fpTrain if r.random()<0.67 else fpTest
          fp.write('\t'.join(['path',x,y]) + '\n')
    return (factFile,trainFile,testFile)

# usage: python logic2tf.py corner-type grid-size num-epochs
#  corner-type: hard, ie train path(X,Y) so Y='1,1'
#  corner-type: soft, ie train path(X,Y) to satisfy a numeric loss function, v_y * (x1+x2)
#    where x1 is x-position, x2 is y-position

def runMain():
  corner = 'hard'
  n = 6
  epochs = 30
  if len(sys.argv)>1:
    corner = sys.argv[1]
  if len(sys.argv)>2:
    n = int(sys.argv[2])
  if len(sys.argv)>3:
    epochs = int(sys.argv[3])
  print 'run',epochs,'epochs',corner,'training on',n,'x',n,'grid','maxdepth',n
  maxD = n/2
  (factFile,trainFile,testFile) = genInputs(n)
  (tlog,trainData,testData) = setup_tlog(maxD,factFile,trainFile,testFile)
  trainAndTest(tlog,trainData,testData,epochs,corner)

if __name__=="__main__":
  runMain()
